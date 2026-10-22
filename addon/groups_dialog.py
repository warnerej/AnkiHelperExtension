"""Qt dialog for managing groups. Decisions live in `groups_logic`; calls go through
the injected `run_in_background`, so nothing here blocks the UI thread."""

from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from aqt.qt import (
    QApplication,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    Qt,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import askUser, showWarning, tooltip

from . import groups_logic as gl
from .api_client import ApiClient
from .dialogs import alive
from .errors import AnkiHelperError
from .log import get_logger
from .models import Group, GroupSummary

log = get_logger()

TITLE = "AnkiHelper: Groups"
_ID_ROLE = Qt.ItemDataRole.UserRole


class GroupsDialog(QDialog):
    def __init__(
        self,
        parent: Any,
        client: ApiClient,
        run_in_background: Callable[[Callable[[], Any], Callable], object],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(760, 520)
        self._client = client
        self._run = run_in_background
        self._group: Group | None = None
        self._roles: dict[str, str] = {}  # group id -> the viewer's role
        self._busy = False

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection)
        create = QPushButton("Create group...")
        create.clicked.connect(self._create)
        join = QPushButton("Join group...")
        join.clicked.connect(self._join)
        left = QVBoxLayout()
        left.addWidget(QLabel("My groups"))
        left.addWidget(self._list)
        left.addWidget(create)
        left.addWidget(join)

        self._title = QLabel("Select a group")
        self._title.setTextFormat(
            Qt.TextFormat.PlainText
        )  # group names come from the server
        self._title.setStyleSheet("font-weight: bold;")
        self._members = QListWidget()
        self._leave = QPushButton("Leave group")
        self._leave.clicked.connect(self._leave_group)

        self._owner_box = QGroupBox("Owner controls")
        self._code = QLineEdit()
        self._code.setReadOnly(True)
        copy = QPushButton("Copy")
        copy.clicked.connect(self._copy_code)
        regen = QPushButton("Regenerate")
        regen.clicked.connect(self._regenerate)
        code_row = QHBoxLayout()
        code_row.addWidget(QLabel("Invite code"))
        code_row.addWidget(self._code, 1)
        code_row.addWidget(copy)
        code_row.addWidget(regen)
        self._allowed = QListWidget()
        add = QPushButton("Add username...")
        add.clicked.connect(self._add_allowed)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove_allowed)
        allowed_row = QHBoxLayout()
        allowed_row.addWidget(add)
        allowed_row.addWidget(remove)
        rename = QPushButton("Rename...")
        rename.clicked.connect(self._rename)
        transfer = QPushButton("Transfer ownership...")
        transfer.clicked.connect(self._transfer)
        delete = QPushButton("Delete group...")
        delete.clicked.connect(self._delete)
        manage_row = QHBoxLayout()
        for button in (rename, transfer, delete):
            manage_row.addWidget(button)
        owner_layout = QVBoxLayout(self._owner_box)
        owner_layout.addLayout(code_row)
        owner_layout.addWidget(QLabel("Allowed usernames (they join with the code)"))
        owner_layout.addWidget(self._allowed)
        owner_layout.addLayout(allowed_row)
        owner_layout.addLayout(manage_row)

        right = QVBoxLayout()
        right.addWidget(self._title)
        right.addWidget(QLabel("Members"))
        right.addWidget(self._members)
        right.addWidget(self._leave)
        right.addWidget(self._owner_box)
        self._details = QWidget()
        self._details.setLayout(right)
        self._details.setVisible(False)

        layout = QHBoxLayout(self)
        layout.addLayout(left, 1)
        layout.addWidget(self._details, 2)

    # --- plumbing -----------------------------------------------------------------

    def _call(
        self,
        work: Callable[[], Any],
        done: Callable[[Any], None],
        *,
        mutation: bool = False,
        refresh_after_failure: bool = False,
    ) -> None:
        """Run `work` off-thread with the dialog disabled; `done` gets the result."""
        if self._busy:
            log.info("groups dialog: ignoring a request made while one is running")
            return
        self._busy = True
        self.setEnabled(False)

        def finished(future: Future) -> None:
            self._busy = False
            if not alive(self):
                log.info("groups dialog closed before its request finished")
                return
            self.setEnabled(True)
            try:
                result = future.result()
            except Exception as exc:
                if not isinstance(exc, AnkiHelperError):
                    log.exception("unexpected error in groups dialog")
                showWarning(
                    gl.error_message(exc, mutation=mutation), title=TITLE, parent=self
                )
                if refresh_after_failure and gl.needs_refresh(exc):
                    self.reload()
                return
            done(result)

        self._run(work, finished)

    def reload(self, select_id: str | None = None) -> None:
        keep = select_id or (self._group.id if self._group else None)
        self._call(
            self._client.list_groups, lambda groups: self._fill_list(groups, keep)
        )

    def _fill_list(self, groups: list[GroupSummary], select_id: str | None) -> None:
        self._roles = {g.id: g.role for g in groups}
        self._list.blockSignals(True)  # one explicit load below, not one per signal
        self._list.clear()
        row_to_select = -1
        for i, g in enumerate(groups):
            item = QListWidgetItem(f"{g.name}  ({g.role}, {g.member_count} members)")
            item.setData(_ID_ROLE, g.id)
            self._list.addItem(item)
            if g.id == select_id:
                row_to_select = i
        if row_to_select >= 0:
            self._list.setCurrentRow(row_to_select)
        self._list.blockSignals(False)
        if row_to_select >= 0:
            self._load_selected()
        else:
            self._group = None
            self._details.setVisible(False)

    def _on_selection(self, current: QListWidgetItem | None, _previous: Any) -> None:
        if current is None:
            self._group = None
            self._details.setVisible(False)
            return
        self._load_selected()

    def _selected_id(self) -> str | None:
        item = self._list.currentItem()
        return item.data(_ID_ROLE) if item else None

    def _load_selected(self) -> None:
        group_id = self._selected_id()
        if group_id is None:
            return
        self._call(lambda: self._client.get_group(group_id), self._show_group)

    def _show_group(self, group: Group) -> None:
        if group.id != self._selected_id():
            return  # selection moved on while loading
        self._group = group
        controls = gl.detail_controls(group, self._roles.get(group.id))
        self._title.setText(f"{group.name}  (owner: {group.owner})")
        self._members.clear()
        for m in group.members:
            self._members.addItem(f"{m.username}  ({m.role})")
        self._leave.setVisible(controls.leave)
        self._owner_box.setVisible(controls.invite_code)
        self._code.setText(group.invite_code or "")
        self._allowed.clear()
        for name in group.allowed or []:
            self._allowed.addItem(name)
        self._details.setVisible(True)

    def _ask_text(self, label: str, text: str = "") -> str | None:
        value, ok = QInputDialog.getText(self, TITLE, label, text=text)
        return value if ok else None

    def _invalid(self, problem: str) -> None:
        showWarning(problem, title=TITLE, parent=self)

    # --- actions ------------------------------------------------------------------

    def _create(self) -> None:
        raw = self._ask_text("Group name")
        if raw is None:
            return
        name, problem = gl.validate_group_name(raw)
        if problem:
            self._invalid(problem)
            return
        self._call(
            lambda: self._client.create_group(name),
            lambda g: self.reload(g.id),
            mutation=True,
            refresh_after_failure=True,
        )

    def _join(self) -> None:
        raw = self._ask_text("Invite code")
        if raw is None:
            return
        code, problem = gl.validate_invite_code(raw)
        if problem:
            self._invalid(problem)
            return
        if not askUser(gl.JOIN_CONFIRMATION, parent=self, title=TITLE):
            return
        self._call(
            lambda: self._client.join_group(code),
            lambda g: self.reload(g.id),
            mutation=True,
            refresh_after_failure=True,
        )

    def _leave_group(self) -> None:
        group = self._group
        if group is None or not askUser(
            gl.LEAVE_CONFIRMATION, parent=self, title=TITLE
        ):
            return
        self._call(
            lambda: self._client.leave_group(group.id),
            lambda _: self._after_removal(),
            mutation=True,
            refresh_after_failure=True,
        )

    def _after_removal(self) -> None:
        self._group = None
        self._details.setVisible(False)
        self.reload()

    def _copy_code(self) -> None:
        if self._group and self._group.invite_code:
            clipboard = QApplication.clipboard()
            assert clipboard is not None
            clipboard.setText(self._group.invite_code)
            tooltip("Invite code copied", parent=self)

    def _regenerate(self) -> None:
        group = self._group
        if group is None or not askUser(
            "Regenerate the invite code? The old code stops working.",
            parent=self,
            title=TITLE,
        ):
            return
        self._call(
            lambda: self._client.regenerate_invite_code(group.id),
            lambda _: self.reload(group.id),
            mutation=True,
            refresh_after_failure=True,
        )

    def _add_allowed(self) -> None:
        group = self._group
        if group is None:
            return
        raw = self._ask_text("Username to allow")
        if raw is None:
            return
        username, problem = gl.validate_username(raw)
        if problem:
            self._invalid(problem)
            return
        self._call(
            lambda: self._client.allow_username(group.id, username),
            lambda _: self.reload(group.id),
            mutation=True,
            refresh_after_failure=True,
        )

    def _remove_allowed(self) -> None:
        group = self._group
        item = self._allowed.currentItem()
        if group is None or item is None:
            return
        username = item.text()
        if not gl.can_remove_allowed(group, username):
            self._invalid("You can't remove the owner from the allowed list.")
            return
        if not askUser(
            gl.remove_allowed_confirmation(group, username), parent=self, title=TITLE
        ):
            return
        self._call(
            lambda: self._client.disallow_username(group.id, username),
            lambda _: self.reload(group.id),
            mutation=True,
            refresh_after_failure=True,
        )

    def _rename(self) -> None:
        group = self._group
        if group is None:
            return
        raw = self._ask_text("New name", group.name)
        if raw is None:
            return
        name, problem = gl.validate_group_name(raw)
        if problem:
            self._invalid(problem)
            return
        self._call(
            lambda: self._client.rename_group(group.id, name),
            lambda _: self.reload(group.id),
            mutation=True,
            refresh_after_failure=True,
        )

    def _transfer(self) -> None:
        group = self._group
        if group is None:
            return
        candidates = gl.transfer_candidates(group)
        if not candidates:
            self._invalid("No other member has joined this group yet.")
            return
        username, ok = QInputDialog.getItem(
            self, TITLE, "New owner", candidates, 0, False
        )
        if not ok:
            return
        if not askUser(
            f"Make {username} the owner of '{group.name}'? You will become a member.",
            parent=self,
            title=TITLE,
        ):
            return
        self._call(
            lambda: self._client.transfer_ownership(group.id, username),
            lambda _: self.reload(group.id),
            mutation=True,
            refresh_after_failure=True,
        )

    def _delete(self) -> None:
        group = self._group
        if group is None:
            return
        typed = self._ask_text(gl.delete_prompt(group))
        if typed is None:
            return
        if not gl.delete_confirmed(group, typed):
            self._invalid("That didn't match the group name; nothing was deleted.")
            return
        self._call(
            lambda: self._client.delete_group(group.id),
            lambda _: self._after_removal(),
            mutation=True,
            refresh_after_failure=True,
        )
