from .services.deletion import is_supervisor, role_label


def role_context(request):
    """把当前登录用户的角色注入模板（角色徽标 / 删除入口提示）。"""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"is_supervisor": False, "role_label": ""}
    return {
        "is_supervisor": is_supervisor(user),
        "role_label": role_label(user),
    }
