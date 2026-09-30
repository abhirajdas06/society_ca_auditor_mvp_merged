"""Server-side role enforcement. Hidden buttons are not security: every view declares the
capability it needs and this module checks it against the user's society membership."""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import Society, SocietyMembership

CA = SocietyMembership.CA
OPERATOR = SocietyMembership.OPERATOR
ADMIN = SocietyMembership.SOCIETY_ADMIN
AUDITOR = SocietyMembership.AUDITOR

CAPABILITIES = {
    "view": {CA, OPERATOR, ADMIN, AUDITOR},
    "audit.view": {CA, ADMIN, AUDITOR},
    # Flats, wings, members.
    "master.edit": {CA, ADMIN, OPERATOR},
    # Society settings, charge heads, charge rules: anything that changes what gets billed.
    "config.edit": {CA, ADMIN},
    # Maker: create periods, generate draft bills, confirm variable lines, submit for review.
    "billing.prepare": {CA, OPERATOR},
    # Checker: approve/issue/lock periods, issue or cancel bills.
    "billing.approve": {CA, ADMIN},
    "receipt.create": {CA, OPERATOR},
    "receipt.clear": {CA, OPERATOR},
    "receipt.bounce": {CA, OPERATOR},
    "receipt.cancel": {CA},
    "allocation.apply": {CA, OPERATOR},
    "allocation.correct": {CA},
    "import.run": {CA, ADMIN},
}

SESSION_KEY = "active_society_id"


def memberships(user):
    if not user.is_authenticated:
        return SocietyMembership.objects.none()
    return SocietyMembership.objects.filter(user=user, is_active=True, society__is_active=True).select_related("society")


def accessible_societies(user):
    if user.is_superuser:
        return Society.objects.filter(is_active=True)
    return Society.objects.filter(pk__in=memberships(user).values("society_id"))


def role_for(user, society):
    if society is None or not user.is_authenticated:
        return None
    if user.is_superuser:
        return CA
    membership = memberships(user).filter(society=society).first()
    return membership.role if membership else None


def can(user, society, capability):
    return role_for(user, society) in CAPABILITIES[capability]


def resolve_society(request):
    """Active society from the session, falling back to the user's first accessible society."""
    societies = accessible_societies(request.user)
    society_id = request.session.get(SESSION_KEY)
    society = societies.filter(pk=society_id).first() if society_id else None
    if society is None:
        society = societies.order_by("name").first()
        if society:
            request.session[SESSION_KEY] = society.pk
    return society


def require(capability, session_only=False):
    """Decorator: login + active society + capability.

    Auditors can never POST, because read-only must hold even if a capability is mis-declared.
    session_only=True marks an action that touches the user's own session and no application data
    (switching the active society), which read-only roles must still be able to do.
    """
    if capability not in CAPABILITIES:
        raise ValueError(f"Unknown capability {capability}")

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            society = resolve_society(request)
            if society is None:
                if capability == "view":
                    request.society, request.role = None, None
                    return view(request, *args, **kwargs)
                return redirect("dashboard")
            role = role_for(request.user, society)
            if role not in CAPABILITIES[capability]:
                raise PermissionDenied(f"Your role cannot perform this action ({capability}).")
            if request.method not in ("GET", "HEAD", "OPTIONS") and role == AUDITOR and not session_only:
                raise PermissionDenied("Auditor access is read-only.")
            request.society, request.role = society, role
            return view(request, *args, **kwargs)

        wrapped.required_capability = capability
        return wrapped

    return decorator


def context(request):
    """Template context processor: exposes role checks so the UI can hide what the server forbids."""
    society = getattr(request, "society", None)
    role = getattr(request, "role", None)
    if not request.user.is_authenticated:
        return {}
    return {
        "active_society": society,
        "active_role": role,
        "role_label": dict(SocietyMembership.ROLE_CHOICES).get(role, ""),
        "perms_can": {cap: role in roles for cap, roles in CAPABILITIES.items()},
        "switchable_societies": accessible_societies(request.user).order_by("name"),
    }
