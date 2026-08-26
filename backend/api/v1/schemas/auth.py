"""Msgspec schemas for auth endpoints."""

from __future__ import annotations

from infrastructure.msgspec_fastapi import AppStruct


class SetupStatusResponse(AppStruct):
    required: bool


class AuthProvidersResponse(AppStruct):
    local: bool
    plex: bool
    jellyfin: bool
    oidc: bool


class SetupRequest(AppStruct):
    display_name: str
    username: str
    password: str
    email: str | None = None


class CreateUserRequest(AppStruct):
    display_name: str
    username: str
    password: str
    role: str = "user"
    email: str | None = None


class LoginRequest(AppStruct):
    username: str
    password: str


class PasswordRecoveryResetRequest(AppStruct):
    username: str
    recovery_code: str
    new_password: str


class PasswordRecoveryCodeResponse(AppStruct):
    recovery_code: str
    expires_at: str


class UserResponse(AppStruct):
    id: str
    display_name: str
    role: str
    email: str | None = None
    avatar_url: str | None = None
    username: str | None = None
    username_display: str | None = None
    providers: list[str] = []
    # None means "not assigned": downloads fall back to the first configured root.
    library_root_id: str | None = None


class AuthResponse(AppStruct):
    token: str
    user: UserResponse


class SessionResponse(AppStruct):
    id: str
    issued_at: str
    expires_at: str
    last_seen_at: str
    user_agent: str | None = None


class SessionListResponse(AppStruct):
    sessions: list[SessionResponse]


class UserListResponse(AppStruct):
    users: list[UserResponse]
    total: int


class SetRoleRequest(AppStruct):
    role: str


class SetLibraryRootRequest(AppStruct):
    """Which library root a user's downloads import into. ``None`` clears the
    assignment, so that user falls back to the first configured root."""

    library_root_id: str | None = None


class LibraryRootAssignment(AppStruct):
    """One selectable root, plus who already holds it. Roots are not exclusive -
    several users may share one - but the settings UI shows current holders so an
    admin can see that before assigning."""

    id: str
    label: str
    path: str
    assigned_user_ids: list[str] = []


class UserQuotaOverrideBody(AppStruct):
    """Per-user quota override (CollectionManagement Feature C). ``None`` inherits
    the global default from the download policy; 0 = unlimited."""

    request_quota_count: int | None = None
    request_quota_days: int | None = None
    storage_quota_gb: int | None = None


class UserQuotaResponse(AppStruct):
    user_id: str
    override: UserQuotaOverrideBody
    effective_request_quota_count: int
    effective_request_quota_days: int
    effective_storage_quota_gb: int
    requests_in_window: int
    storage_bytes: int
    exempt: bool


class ImportCandidateResponse(AppStruct):
    provider: str
    provider_uid: str
    display_name: str
    avatar_url: str | None = None
    email: str | None = None
    already_imported: bool = False


class ImportCandidateListResponse(AppStruct):
    users: list[ImportCandidateResponse]


class ImportUsersRequest(AppStruct):
    provider: str
    provider_uids: list[str]


class ImportUsersResponse(AppStruct):
    imported: list[UserResponse]
    linked: list[UserResponse]
    skipped: list[str]
    total_imported: int


class PlexPinResponse(AppStruct):
    pin_id: int
    auth_url: str


class PlexPollResponse(AppStruct):
    completed: bool
    token: str | None = None
    user: UserResponse | None = None


class JellyfinLoginRequest(AppStruct):
    username: str
    password: str


class OIDCAuthorizeResponse(AppStruct):
    redirect_url: str


class OIDCExchangeRequest(AppStruct):
    code: str


def user_to_response(user, providers: list[str] | None = None) -> UserResponse:
    return UserResponse(
        id = user.id,
        display_name = user.display_name,
        role = user.role,
        email = user.email,
        avatar_url = user.avatar_url,
        username = user.username,
        username_display = user.username_display,
        providers = providers or [],
        library_root_id = getattr(user, "library_root_id", None),
    )


def import_candidate_to_response(candidate) -> ImportCandidateResponse:
    return ImportCandidateResponse(
        provider = candidate.provider,
        provider_uid = candidate.provider_uid,
        display_name = candidate.display_name,
        avatar_url = candidate.avatar_url,
        email = candidate.email,
        already_imported = candidate.already_imported,
    )


def session_to_response(token) -> SessionResponse:
    return SessionResponse(
        id = token.id,
        issued_at = token.issued_at,
        expires_at = token.expires_at,
        last_seen_at = token.last_seen_at,
        user_agent = token.user_agent,
    )
