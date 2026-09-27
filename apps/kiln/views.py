from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import OpenCookRunForm, PhaseChangeForm, ResinLotForm, SoftPointProbeForm
from .models import CookRun, FireHearth, ResinLot, SoftPointProbe
from .services.deletion import (
    delete_entity,
    hearth_block_reason,
    resin_lot_block_reason,
)
from .services.floor_rules import change_hearth_phase


def _wants_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _hearths_for_board():
    return FireHearth.objects.prefetch_related(
        Prefetch(
            "runs",
            queryset=CookRun.objects.filter(closedAt__isnull=True)
            .select_related("resinLot")
            .prefetch_related("probes"),
            to_attr="open_runs_cache",
        )
    ).order_by("lane", "tag")


def _board_context():
    hearths = list(_hearths_for_board())
    lanes = {}
    for h in hearths:
        lanes.setdefault(h.lane, []).append(h)
    phase_legend = [
        (key, label, sum(1 for h in hearths if h.phase == key))
        for key, label in FireHearth.PHASE_CHOICES
    ]
    return {
        "hearths": hearths,
        "lanes": sorted(lanes.items()),
        "phase_legend": phase_legend,
    }


def _drawer_context(request, hearth, partial=False):
    open_run = hearth.open_run()
    probes = []
    if open_run:
        probes = list(open_run.probes.order_by("-sampledAt", "-id"))
    return {
        "hearth": hearth,
        "open_run": open_run,
        "probes": probes,
        "phase_form": PhaseChangeForm(hearth=hearth),
        "probe_form": SoftPointProbeForm() if open_run else None,
        "open_run_form": OpenCookRunForm(hearth=hearth) if open_run is None else None,
        # 与后端 deletion 服务同一套守卫：有未收灶值守则前端置灰
        "hearth_delete_block": hearth_block_reason(open_run is not None),
        "partial_messages": partial,
    }


def _lot_feed_context(partial=False):
    lots = list(
        ResinLot.objects.annotate(
            open_run_count=Count("runs", filter=Q(runs__closedAt__isnull=True))
        )[:40]
    )
    for lot in lots:
        # 与后端 deletion 服务同一套守卫文案
        lot.delete_block = resin_lot_block_reason(lot.open_run_count)
    return {"lots": lots, "partial_messages": partial}


@login_required
def home(request):
    ctx = _board_context()
    drawer_pk = request.GET.get("hearth")
    if drawer_pk:
        try:
            hearth = FireHearth.objects.get(pk=drawer_pk)
            ctx.update(_drawer_context(request, hearth))
            ctx["drawer_open"] = True
        except (FireHearth.DoesNotExist, ValueError):
            ctx["drawer_open"] = False
    else:
        ctx["drawer_open"] = False
    return render(request, "floor/board.html", ctx)


@login_required
def floor_grid_partial(request):
    ctx = _board_context()
    ctx["oob_legend"] = True
    html = render_to_string("floor/_grid.html", ctx, request=request)
    return HttpResponse(html)


@login_required
def hearth_drawer(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    if _wants_htmx(request):
        return render(
            request, "floor/_drawer.html", _drawer_context(request, hearth, partial=True)
        )
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def change_phase(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    form = PhaseChangeForm(request.POST, hearth=hearth)
    if form.is_valid():
        try:
            change_hearth_phase(hearth, form.cleaned_data["phase"])
            messages.success(request, f"灶牌 {hearth.tag} 相位已更新")
        except ValidationError as exc:
            msg = (
                exc.message_dict.get("phase") if hasattr(exc, "message_dict") else None
            )
            messages.error(request, msg[0] if msg else str(exc))
    else:
        err = form.errors.get("phase")
        messages.error(request, err[0] if err else "相位切换失败")

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(
            request, "floor/_drawer.html", _drawer_context(request, hearth, partial=True)
        )
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def add_probe(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守，无法登记探针")
        return redirect(f"/?hearth={pk}")

    form = SoftPointProbeForm(request.POST)
    if form.is_valid():
        probe = form.save(commit=False)
        probe.run = open_run
        probe.save()
        messages.success(request, f"已登记探针 {probe.softPointC}℃")
    else:
        messages.error(request, "探针登记失败，请检查输入")

    if _wants_htmx(request):
        resp = render(
            request, "floor/_drawer.html", _drawer_context(request, hearth, partial=True)
        )
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def open_run(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    form = OpenCookRunForm(request.POST, hearth=hearth)
    if form.is_valid():
        run = form.save(commit=False)
        run.hearth = hearth
        run.save()
        if hearth.phase == FireHearth.PHASE_COLD:
            hearth.phase = FireHearth.PHASE_CHARGING
            hearth.save(update_fields=["phase"])
        messages.success(request, "新值守已开灶")
    else:
        for errs in form.errors.values():
            for e in errs:
                messages.error(request, e)
            break

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(
            request, "floor/_drawer.html", _drawer_context(request, hearth, partial=True)
        )
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def close_run(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守可收灶")
    else:
        open_run.closedAt = timezone.now()
        open_run.save(update_fields=["closedAt"])
        hearth.phase = FireHearth.PHASE_COLD
        hearth.save(update_fields=["phase"])
        messages.success(request, "值守已收灶，灶台回冷灶")

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(
            request, "floor/_drawer.html", _drawer_context(request, hearth, partial=True)
        )
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_http_methods(["GET", "POST"])
def resin_lot_feed(request):
    if request.method == "POST":
        form = ResinLotForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "来脂批已登记")
            return redirect("resin_lot_feed")
    else:
        form = ResinLotForm(
            initial={
                "receivedAt": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            }
        )

    ctx = _lot_feed_context()
    ctx["form"] = form
    return render(request, "resin/feed.html", ctx)


# 四类删除共用一个入口：角色与守卫全部收敛在 services/deletion.py
DELETE_KINDS = {
    "resin-lot": ResinLot,
    "hearth": FireHearth,
    "cook-run": CookRun,
    "probe": SoftPointProbe,
}


@login_required
@require_POST
def delete_entity_view(request, kind, pk):
    model = DELETE_KINDS.get(kind)
    if model is None:
        raise Http404("未知的删除类型")
    obj = get_object_or_404(model, pk=pk)

    # 删除前先记住联动刷新所需的灶台（对象删了就查不到了）
    hearth = None
    if isinstance(obj, FireHearth):
        hearth = obj
    elif isinstance(obj, CookRun):
        hearth = obj.hearth
    elif isinstance(obj, SoftPointProbe):
        hearth = obj.run.hearth

    deleted = False
    try:
        messages.success(request, delete_entity(request.user, obj))
        deleted = True
    except ValidationError as exc:
        messages.error(request, exc.messages[0])

    if _wants_htmx(request):
        if kind == "resin-lot":
            # 重渲染整条流：「共 N 批」与卡片行数同源对齐
            return render(
                request, "resin/_lot_feed.html", _lot_feed_context(partial=True)
            )
        if kind == "hearth" and deleted:
            resp = render(request, "floor/_drawer_empty.html")
            resp["HX-Trigger"] = "floor-refresh"
            return resp
        if hearth is not None:
            hearth.refresh_from_db()
            resp = render(
                request,
                "floor/_drawer.html",
                _drawer_context(request, hearth, partial=True),
            )
            resp["HX-Trigger"] = "floor-refresh"
            return resp
    if kind == "resin-lot":
        return redirect("resin_lot_feed")
    return redirect("home")
