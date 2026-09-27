"""四类删除（来脂批 / 灶台 / 值守 / 探针）的统一入口。

角色分流：值守工可建可改、不可删；删除权只给主管。
所有删除动作都走 ``delete_entity``：先验角色，再跑模型守卫，最后删除。
模板置灰按钮与后端拒绝用的是同一套 ``*_block_reason`` 函数，
保证「前后端同一套规则」，不允许四类各写各的。
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.kiln.models import CookRun, FireHearth, ResinLot, SoftPointProbe

WORKER_DELETE_DENIED = "仅主管可删除：值守工可建可改，但无删除权限。"


def is_supervisor(user) -> bool:
    """主管 = 超级用户或 staff；其余登录用户皆为值守工。"""
    return bool(
        user and user.is_authenticated and (user.is_superuser or user.is_staff)
    )


def resin_lot_block_reason(open_run_count: int) -> str | None:
    """来脂批守卫：仍挂未收灶值守则不可删，须先收灶清空。"""
    if open_run_count:
        return f"来脂批仍挂 {open_run_count} 条未收灶值守，请先收灶清空后再删。"
    return None


def hearth_block_reason(has_open_run: bool) -> str | None:
    """灶台守卫：有未收灶值守则不可删（说明文档与前后端共用此条）。"""
    if has_open_run:
        return "灶台仍有未收灶值守，请先收灶后再删。"
    return None


def _guard_resin_lot(lot: ResinLot) -> None:
    reason = resin_lot_block_reason(
        lot.runs.filter(closedAt__isnull=True).count()
    )
    if reason:
        raise ValidationError(reason)


def _guard_hearth(hearth: FireHearth) -> None:
    reason = hearth_block_reason(hearth.open_run() is not None)
    if reason:
        raise ValidationError(reason)


# 模型 → 业务守卫（与角色无关）；None 表示无额外守卫
_BUSINESS_GUARDS = {
    ResinLot: _guard_resin_lot,
    FireHearth: _guard_hearth,
    CookRun: None,
    SoftPointProbe: None,
}


def business_block_reason(obj) -> str | None:
    """业务守卫原因（不含角色）；None 表示业务上可删。"""
    guard = _BUSINESS_GUARDS[type(obj)]
    if guard is None:
        return None
    try:
        guard(obj)
    except ValidationError as exc:
        return exc.messages[0]
    return None


def delete_block_reason(user, obj) -> str | None:
    """完整拦截原因：先角色、后业务；None 表示可删。"""
    if not is_supervisor(user):
        return WORKER_DELETE_DENIED
    return business_block_reason(obj)


def delete_entity(user, obj) -> str:
    """统一删除入口：被拦则抛 ValidationError（中文原因），否则删除并返回提示。"""
    reason = delete_block_reason(user, obj)
    if reason is not None:
        raise ValidationError(reason)
    with transaction.atomic():
        if isinstance(obj, ResinLot):
            # 已收灶的值守连同探针随批一并清除
            # （CookRun.resinLot 为 PROTECT，须先清子行再删批）
            obj.runs.filter(closedAt__isnull=False).delete()
        label = f"{obj._meta.verbose_name} {obj}"
        obj.delete()
    return f"已删除{label}"
