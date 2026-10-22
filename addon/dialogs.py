from collections.abc import Callable
from typing import Any

from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    Qt,
    QVBoxLayout,
)
from aqt.utils import showWarning

from .delete_logic import DELETE_WARNING, validate_delete
from .history_logic import CONFIRM_WORD, typed_confirmation_ok
from .settings import USERNAME_RE


class CredentialsDialog(QDialog):
    """Email + password, plus a username when signing up. Validates before accepting."""

    def __init__(
        self,
        parent: Any,
        title: str,
        *,
        signup: bool,
        on_forgot: Callable[[str, Any], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._signup = signup
        self._forgot: QPushButton | None = None
        self.email = QLineEdit()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow("Email", self.email)
        if signup:
            form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        if signup:
            layout.addWidget(
                hint("Username: 3-20 letters, digits or _. Password: 8+ characters.")
            )
        if on_forgot is not None and not signup:
            self._forgot = QPushButton("Forgot password?")
            self._forgot.setFlat(True)
            self._forgot.setAutoDefault(False)
            self._forgot.setCursor(Qt.CursorShape.PointingHandCursor)
            self._forgot.clicked.connect(
                lambda: on_forgot(self.email.text().strip(), self)
            )
            layout.addWidget(self._forgot, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(buttons)

    def set_forgot_enabled(self, enabled: bool) -> None:
        if self._forgot is not None:
            self._forgot.setEnabled(enabled)

    def _validate(self) -> None:
        problem = None
        if not self.email.text().strip() or not self.password.text():
            problem = "Enter your email and password."
        elif self._signup and not USERNAME_RE.fullmatch(
            self.username.text().strip().lower()
        ):
            problem = "Username must be 3-20 letters, digits or _."
        elif self._signup and len(self.password.text()) < 8:
            problem = "Password must be at least 8 characters."
        if problem:
            showWarning(problem, title="AnkiHelper", parent=self)
            return
        self.accept()


class DeleteAccountDialog(QDialog):
    """Account deletion confirmation: type the account email and the password."""

    def __init__(self, parent: Any, expected_email: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("AnkiHelper: Delete account")
        self._expected_email = expected_email
        self.email = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow(f"Type {expected_email} to confirm", self.email)
        form.addRow("Password", self.password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText("Delete account")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint(DELETE_WARNING))
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        problem = validate_delete(
            self._expected_email, self.email.text(), self.password.text()
        )
        if problem:
            showWarning(problem, title="AnkiHelper", parent=self)
            return
        self.accept()


class EmptyUploadDialog(QDialog):
    """Stronger confirm for uploading an empty year: type the word to proceed."""

    def __init__(self, parent: Any, message: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("AnkiHelper: Re-upload review history")
        self.typed = QLineEdit()
        form = QFormLayout()
        form.addRow(f"Type {CONFIRM_WORD} to confirm", self.typed)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint(message))
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        if not typed_confirmation_ok(self.typed.text()):
            showWarning(
                f"Type {CONFIRM_WORD} to confirm, or cancel.",
                title="AnkiHelper",
                parent=self,
            )
            return
        self.accept()


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def alive(widget: Any) -> bool:
    """False once Qt has deleted the widget's C++ object (e.g. the dialog was closed)."""
    try:
        widget.isVisible()
    except RuntimeError:
        return False
    return True
