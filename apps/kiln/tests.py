from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe
from .seed import ensure_seed_data
from .services.deletion import WORKER_DELETE_DENIED

HTMX = {"HTTP_HX_REQUEST": "true"}


class DeleteFlowTests(TestCase):
    """角色分流：值守工可建可改不可删；删除权只给主管。"""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.boss = User.objects.create_superuser("boss", "boss@pitchkiln.local", "pw")
        cls.worker = User.objects.create_user("worker", "w@pitchkiln.local", "pw")
        now = timezone.now()
        cls.lot_busy = ResinLot.objects.create(
            lotCode="脂-松脂坳-T01",
            originPlace="松脂坳东沟",
            arrivalKg=Decimal("100.00"),
            receivedAt=now,
        )
        cls.lot_free = ResinLot.objects.create(
            lotCode="脂-桐油坑-T02",
            originPlace="桐油坑北坡",
            arrivalKg=Decimal("50.00"),
            receivedAt=now,
        )
        cls.hearth = FireHearth.objects.create(
            lane=1, tag="坳火-测", resinGrade="特级脂"
        )
        cls.cook_run = CookRun.objects.create(
            hearth=cls.hearth,
            resinLot=cls.lot_busy,
            openedAt=now,
            targetSoftPointC=Decimal("88.00"),
        )
        cls.probe = SoftPointProbe.objects.create(
            run=cls.cook_run,
            sampledAt=now,
            softPointC=Decimal("96.20"),
            samplerName="值守小测",
        )

    def _url(self, kind, obj):
        return reverse("delete_entity", args=[kind, obj.pk])

    def _close_run(self):
        self.cook_run.closedAt = timezone.now()
        self.cook_run.save(update_fields=["closedAt"])

    # —— 登录鉴权保持 ——
    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.post(self._url("probe", self.probe))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])
        self.assertTrue(SoftPointProbe.objects.filter(pk=self.probe.pk).exists())

    def test_delete_requires_post(self):
        self.client.force_login(self.boss)
        resp = self.client.get(self._url("probe", self.probe))
        self.assertEqual(resp.status_code, 405)

    def test_unknown_kind_is_404(self):
        self.client.force_login(self.boss)
        resp = self.client.post(reverse("delete_entity", args=["nope", 1]))
        self.assertEqual(resp.status_code, 404)

    # —— 值守工：任何删除入口都中文挡下 ——
    def test_worker_blocked_on_all_four_kinds(self):
        self.client.force_login(self.worker)
        cases = [
            ("resin-lot", self.lot_free, ResinLot),
            ("hearth", self.hearth, FireHearth),
            ("cook-run", self.cook_run, CookRun),
            ("probe", self.probe, SoftPointProbe),
        ]
        for kind, obj, model in cases:
            with self.subTest(kind=kind):
                resp = self.client.post(self._url(kind, obj), follow=True)
                self.assertContains(resp, "仅主管可删除")
                self.assertTrue(model.objects.filter(pk=obj.pk).exists())

    def test_worker_blocked_via_htmx_sees_chinese(self):
        self.client.force_login(self.worker)
        resp = self.client.post(self._url("resin-lot", self.lot_free), **HTMX)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, WORKER_DELETE_DENIED)
        self.assertTrue(ResinLot.objects.filter(pk=self.lot_free.pk).exists())

    def test_worker_can_still_create(self):
        """可建可改不受影响：值守工仍能登记来脂批。"""
        self.client.force_login(self.worker)
        resp = self.client.post(
            reverse("resin_lot_feed"),
            {
                "lotCode": "脂-松脂坳-T03",
                "originPlace": "松脂坳西岔",
                "arrivalKg": "12.50",
                "receivedAt": "2026-09-26T08:00",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ResinLot.objects.filter(lotCode="脂-松脂坳-T03").exists())

    # —— 主管：来脂批删除守卫 ——
    def test_boss_lot_delete_refused_with_open_run(self):
        self.client.force_login(self.boss)
        resp = self.client.post(self._url("resin-lot", self.lot_busy), follow=True)
        self.assertContains(resp, "未收灶值守")
        self.assertTrue(ResinLot.objects.filter(pk=self.lot_busy.pk).exists())

    def test_boss_lot_delete_allowed_after_clearing_open_runs(self):
        self.client.force_login(self.boss)
        self._close_run()
        resp = self.client.post(self._url("resin-lot", self.lot_busy), follow=True)
        self.assertContains(resp, "已删除")
        self.assertFalse(ResinLot.objects.filter(pk=self.lot_busy.pk).exists())
        # 已收灶的值守与探针随批一并清除
        self.assertFalse(CookRun.objects.filter(pk=self.cook_run.pk).exists())
        self.assertFalse(SoftPointProbe.objects.filter(pk=self.probe.pk).exists())

    def test_boss_lot_delete_free_lot_via_htmx_aligns_count(self):
        """删成功后：流张数与卡片行数对齐。"""
        self.client.force_login(self.boss)
        resp = self.client.get(reverse("resin_lot_feed"))
        self.assertContains(resp, "本流共 2 批")
        self.assertEqual(resp.content.count(b'<li class="lot-card">'), 2)

        resp = self.client.post(self._url("resin-lot", self.lot_free), **HTMX)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "本流共 1 批")
        self.assertEqual(resp.content.count(b'<li class="lot-card">'), 1)
        self.assertContains(resp, "已删除")
        # 卡片流里只剩未删的那批（批号只应出现在成功提示里，不再是卡片）
        self.assertNotContains(
            resp, f'<div class="lot-code mono">{self.lot_free.lotCode}</div>'
        )

    # —— 主管：灶台删除守卫（前后端同一套） ——
    def test_boss_hearth_delete_refused_with_open_run(self):
        self.client.force_login(self.boss)
        resp = self.client.post(self._url("hearth", self.hearth), follow=True)
        self.assertContains(resp, "未收灶值守")
        self.assertTrue(FireHearth.objects.filter(pk=self.hearth.pk).exists())

    def test_drawer_disables_hearth_delete_when_open_run(self):
        """前端置灰原因与后端守卫同一套文案。"""
        self.client.force_login(self.boss)
        resp = self.client.get(
            reverse("hearth_drawer", args=[self.hearth.pk]), **HTMX
        )
        self.assertContains(resp, "灶台仍有未收灶值守，请先收灶后再删。")
        self.assertContains(resp, "disabled")
        self._close_run()
        resp = self.client.get(
            reverse("hearth_drawer", args=[self.hearth.pk]), **HTMX
        )
        self.assertNotContains(resp, "灶台仍有未收灶值守")

    def test_boss_hearth_delete_allowed_after_close(self):
        self.client.force_login(self.boss)
        self._close_run()
        resp = self.client.post(self._url("hearth", self.hearth), **HTMX)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["HX-Trigger"], "floor-refresh")
        self.assertFalse(FireHearth.objects.filter(pk=self.hearth.pk).exists())
        # 值守与探针级联清除
        self.assertFalse(CookRun.objects.filter(pk=self.cook_run.pk).exists())
        self.assertFalse(SoftPointProbe.objects.filter(pk=self.probe.pk).exists())

    # —— 主管：值守 / 探针删除 ——
    def test_boss_run_delete_cascades_probes(self):
        self.client.force_login(self.boss)
        resp = self.client.post(self._url("cook-run", self.cook_run), **HTMX)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(CookRun.objects.filter(pk=self.cook_run.pk).exists())
        self.assertFalse(SoftPointProbe.objects.filter(pk=self.probe.pk).exists())
        # 灶台与来脂批保留
        self.assertTrue(FireHearth.objects.filter(pk=self.hearth.pk).exists())
        self.assertTrue(ResinLot.objects.filter(pk=self.lot_busy.pk).exists())

    def test_boss_probe_delete(self):
        self.client.force_login(self.boss)
        resp = self.client.post(self._url("probe", self.probe), **HTMX)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SoftPointProbe.objects.filter(pk=self.probe.pk).exists())
        self.assertTrue(CookRun.objects.filter(pk=self.cook_run.pk).exists())

    # —— 看板计数随刷新对齐 ——
    def test_grid_partial_refreshes_legend_oob(self):
        self.client.force_login(self.worker)
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.content.count(b'id="phase-legend"'), 1)
        resp = self.client.get(reverse("floor_grid"))
        self.assertContains(resp, 'hx-swap-oob="true"')


class SeedDataTests(TestCase):
    def test_seed_has_lots_with_and_without_runs(self):
        ensure_seed_data()
        busy = ResinLot.objects.get(lotCode="脂-松脂坳-2409A")
        busy_b = ResinLot.objects.get(lotCode="脂-桐油坑-2409B")
        free = ResinLot.objects.get(lotCode="脂-松脂坳-2409C")
        self.assertTrue(busy.runs.filter(closedAt__isnull=True).exists())
        self.assertTrue(busy_b.runs.filter(closedAt__isnull=True).exists())
        self.assertFalse(free.runs.exists())

    def test_seed_roles_and_idempotent(self):
        ensure_seed_data()
        User = get_user_model()
        self.assertTrue(User.objects.get(username="admin").is_superuser)
        worker = User.objects.get(username="worker")
        self.assertFalse(worker.is_staff or worker.is_superuser)
        counts = (
            ResinLot.objects.count(),
            FireHearth.objects.count(),
            CookRun.objects.count(),
        )
        ensure_seed_data()
        self.assertEqual(
            counts,
            (
                ResinLot.objects.count(),
                FireHearth.objects.count(),
                CookRun.objects.count(),
            ),
        )
