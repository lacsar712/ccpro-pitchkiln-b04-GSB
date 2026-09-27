"""四类对象（来脂批 / 灶台 / 值守 / 探针）的统一删除守卫与执行。

角色分流：值守工可建可改，删除权只给主管。
所有删除入口（四个视图、四处模板）都只用本模块这一套判定，
不得各写各的；前端展示的「能否删除」与后端拦截用的是同一个函数。
"""
from django.contrib.auth.models import Group
from django.db import transaction

from apps.kiln.models import CookRun, FireHearth, ResinLot, SoftPointProbe

SUPERVISOR_GROUP = "主管"
WORKER_GROUP = "值守工"

ROLE_REFUSAL = "删除权限仅为主管开放，值守工不可删除。"


class DeleteRefused(Exception):
    """删除被规则拒绝（角色不足或仍有未收灶值守），消息直接展示给用户。"""


def is_supervisor(user) -> bool:
    """主管 = 超级用户或「主管」组成员；其余登录用户均为值守工。"""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=SUPERVISOR_GROUP).exists()


def role_label(user) -> str:
    return SUPERVISOR_GROUP if is_supervisor(user) else WORKER_GROUP


def ensure_role_groups():
    """幂等建立两个角色组（种子数据调用）。"""
    for name in (SUPERVISOR_GROUP, WORKER_GROUP):
        Group.objects.get_or_create(name=name)


def delete_block_reason(obj) -> str | None:
    """业务守卫（与角色无关）：返回不可删的原因，可删则返回 None。

    前后端同一套：模板用它决定按钮是否禁用并展示理由，
    后端 perform_delete 用它做最终拦截。

    - 来脂批：仍挂未收灶值守 → 拒删，清空未收灶后才可删
    - 灶台：仍有未收灶值守 → 拒删（规则写进 README）
    - 值守 / 探针：无额外业务限制
    """
    if isinstance(obj, ResinLot):
        n = obj.runs.filter(closedAt__isnull=True).count()
        if n:
            return f"仍挂 {n} 条未收灶值守，清空未收灶后才可删除"
    elif isinstance(obj, FireHearth):
        if obj.open_run() is not None:
            return "仍有未收灶值守，请先收灶再删除灶台"
    return None


def review_delete(user, obj) -> tuple[bool, str | None]:
    """统一判定：先角色、后业务。返回 (可否删除, 拒绝原因)。"""
    if not is_supervisor(user):
        return False, ROLE_REFUSAL
    reason = delete_block_reason(obj)
    if reason:
        return False, reason
    return True, None


def _object_label(obj) -> str:
    return obj._meta.verbose_name


@transaction.atomic
def perform_delete(user, obj) -> str:
    """四类删除的唯一执行入口。拒绝时抛 DeleteRefused，成功返回提示语。

    级联规则：
    - 来脂批：已收灶的值守（连同探针）随批一并删除（外键 PROTECT，需显式清）
    - 灶台：其下值守与探针随灶级联删除
    - 值守：探针随值守级联删除；若删的是未收灶值守，灶台回冷灶
    - 探针：单条删除
    """
    ok, reason = review_delete(user, obj)
    if not ok:
        raise DeleteRefused(reason)

    label = _object_label(obj)
    name = str(obj)

    if isinstance(obj, ResinLot):
        # 未收灶值守已被 delete_block_reason 拦截，这里只剩已收灶的
        CookRun.objects.filter(resinLot=obj).delete()
        obj.delete()
    elif isinstance(obj, FireHearth):
        obj.delete()  # runs / probes 由外键 CASCADE 一并清掉
    elif isinstance(obj, CookRun):
        hearth = obj.hearth
        was_open = obj.is_open
        obj.delete()  # probes 级联
        if was_open and hearth.phase != FireHearth.PHASE_COLD:
            hearth.phase = FireHearth.PHASE_COLD
            hearth.save(update_fields=["phase"])
    elif isinstance(obj, SoftPointProbe):
        obj.delete()
    else:  # pragma: no cover - 防御未知类型
        raise DeleteRefused("未知对象类型，删除被拒绝。")

    return f"已删除{label}：{name}"
