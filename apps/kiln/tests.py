from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe
from .seed import ensure_seed_data
from .services.deletion import (
    ROLE_REFUSAL,
    SUPERVISOR_GROUP,
    WORKER_GROUP,
    delete_block_reason,
    is_supervisor,
)


class RoleDeleteTests(TestCase):
    """角色分流 + 四类删除同一套规则的端到端校验。"""

    @classmethod
    def setUpTestData(cls):
        ensure_seed_data()

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.get(username="admin")
        self.worker = User.objects.get(username="worker")
        self.lot_with_runs = ResinLot.objects.get(lotCode="脂-松脂坳-2409A")
        self.lot_without_runs = ResinLot.objects.get(lotCode="脂-松脂坳-2409C")
        self.hearth_open = FireHearth.objects.get(tag="坳火-甲")  # 有未收灶值守
        self.run_open = self.hearth_open.open_run()
        self.probe = self.run_open.probes.first()

    # —— 种子与角色 ——

    def test_seed_roles_and_groups(self):
        self.assertTrue(is_supervisor(self.admin))
        self.assertFalse(is_supervisor(self.worker))
        self.assertTrue(self.admin.groups.filter(name=SUPERVISOR_GROUP).exists())
        self.assertTrue(self.worker.groups.filter(name=WORKER_GROUP).exists())

    def test_seed_lots_with_and_without_runs(self):
        """种子：一批有值守、一批无值守。"""
        self.assertTrue(
            ResinLot.objects.get(lotCode="脂-松脂坳-2409A")
            .runs.filter(closedAt__isnull=True)
            .exists()
        )
        self.assertTrue(
            ResinLot.objects.get(lotCode="脂-桐油坑-2409B")
            .runs.filter(closedAt__isnull=True)
            .exists()
        )
        self.assertFalse(self.lot_without_runs.runs.exists())

    # —— 登录鉴权保持 ——

    def test_anonymous_redirected_to_login(self):
        resp = self.client.post(
            reverse("resin_lot_delete", args=[self.lot_without_runs.pk])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])
        self.assertTrue(ResinLot.objects.filter(pk=self.lot_without_runs.pk).exists())

    # —— 值守工：任何删除入口都被中文挡下 ——

    def test_worker_blocked_on_all_four_delete_entries(self):
        self.client.login(username="worker", password="123456")
        cases = [
            ("resin_lot_delete", self.lot_without_runs.pk, ResinLot),
            ("hearth_delete", self.hearth_open.pk, FireHearth),
            ("run_delete", self.run_open.pk, CookRun),
            ("probe_delete", self.probe.pk, SoftPointProbe),
        ]
        for name, pk, model in cases:
            with self.subTest(entry=name):
                resp = self.client.post(reverse(name, args=[pk]), follow=True)
                self.assertContains(resp, ROLE_REFUSAL)
                self.assertTrue(model.objects.filter(pk=pk).exists())

    # —— 主管删来脂批：未收灶值守先拒绝，清空后才可删 ——

    def test_supervisor_delete_lot_blocked_by_open_runs(self):
        self.client.login(username="admin", password="123456")
        resp = self.client.post(
            reverse("resin_lot_delete", args=[self.lot_with_runs.pk]), follow=True
        )
        self.assertContains(resp, "未收灶")
        self.assertTrue(ResinLot.objects.filter(pk=self.lot_with_runs.pk).exists())

    def test_supervisor_delete_lot_after_clearing_open_runs(self):
        self.client.login(username="admin", password="123456")
        lot = self.lot_with_runs
        # 清空未收灶
        lot.runs.filter(closedAt__isnull=True).update(closedAt=timezone.now())
        resp = self.client.post(
            reverse("resin_lot_delete", args=[lot.pk]), follow=True
        )
        self.assertContains(resp, "已删除来脂批")
        self.assertFalse(ResinLot.objects.filter(pk=lot.pk).exists())
        # 已收灶值守与探针随批一并清除
        self.assertFalse(CookRun.objects.filter(resinLot_id=lot.pk).exists())
        self.assertFalse(SoftPointProbe.objects.filter(run__resinLot_id=lot.pk).exists())

    # —— 灶台规则：前后端同一套 ——

    def test_hearth_rule_shared_between_front_and_back(self):
        hearth = self.hearth_open
        # 后端守卫：有未收灶值守 → 不可删
        self.assertIsNotNone(delete_block_reason(hearth))
        # 前端抽屉：同一函数给出的理由渲染在页面上，按钮禁用
        self.client.login(username="admin", password="123456")
        resp = self.client.get(reverse("home"), {"hearth": hearth.pk})
        self.assertContains(resp, delete_block_reason(hearth))
        self.assertContains(resp, "删除灶台")
        # 后端拦截：直接 POST 也被同一理由拒绝
        resp = self.client.post(
            reverse("hearth_delete", args=[hearth.pk]), follow=True
        )
        self.assertContains(resp, "未收灶")
        self.assertTrue(FireHearth.objects.filter(pk=hearth.pk).exists())

    def test_supervisor_delete_hearth_after_close_run(self):
        self.client.login(username="admin", password="123456")
        hearth = FireHearth.objects.get(tag="坑火-西二")  # 冷灶、无值守
        resp = self.client.post(
            reverse("hearth_delete", args=[hearth.pk]), follow=True
        )
        self.assertContains(resp, "已删除灶台")
        self.assertFalse(FireHearth.objects.filter(pk=hearth.pk).exists())

    def test_delete_hearth_cascades_runs_and_probes(self):
        self.client.login(username="admin", password="123456")
        hearth = self.hearth_open
        run_ids = list(hearth.runs.values_list("id", flat=True))
        hearth.runs.filter(closedAt__isnull=True).update(closedAt=timezone.now())
        self.client.post(reverse("hearth_delete", args=[hearth.pk]))
        self.assertFalse(CookRun.objects.filter(id__in=run_ids).exists())
        self.assertFalse(SoftPointProbe.objects.filter(run_id__in=run_ids).exists())

    # —— 值守 / 探针删除 ——

    def test_supervisor_delete_open_run_cools_hearth(self):
        self.client.login(username="admin", password="123456")
        hearth = self.hearth_open
        run = self.run_open
        self.assertNotEqual(hearth.phase, FireHearth.PHASE_COLD)
        resp = self.client.post(reverse("run_delete", args=[run.pk]), follow=True)
        self.assertContains(resp, "已删除")
        self.assertFalse(CookRun.objects.filter(pk=run.pk).exists())
        self.assertFalse(SoftPointProbe.objects.filter(run_id=run.pk).exists())
        hearth.refresh_from_db()
        self.assertEqual(hearth.phase, FireHearth.PHASE_COLD)

    def test_supervisor_delete_probe(self):
        self.client.login(username="admin", password="123456")
        resp = self.client.post(
            reverse("probe_delete", args=[self.probe.pk]), follow=True
        )
        self.assertContains(resp, "已删除")
        self.assertFalse(SoftPointProbe.objects.filter(pk=self.probe.pk).exists())

    # —— 删成功后流张数与卡片行数对齐 ——

    def test_feed_count_matches_card_rows_after_delete(self):
        self.client.login(username="admin", password="123456")
        before = ResinLot.objects.count()
        resp = self.client.get(reverse("resin_lot_feed"))
        self.assertContains(resp, f"共 {before} 张")
        self.assertEqual(resp.content.count(b'<li class="lot-card">'), before)

        self.client.post(
            reverse("resin_lot_delete", args=[self.lot_without_runs.pk])
        )
        after = ResinLot.objects.count()
        self.assertEqual(after, before - 1)
        resp = self.client.get(reverse("resin_lot_feed"))
        self.assertContains(resp, f"共 {after} 张")
        self.assertEqual(resp.content.count(b'<li class="lot-card">'), after)

    # —— 建 / 改仍对值守工开放 ——

    def test_worker_can_still_create_and_update(self):
        self.client.login(username="worker", password="123456")
        resp = self.client.post(
            reverse("resin_lot_feed"),
            {
                "lotCode": "脂-松脂坳-2409D",
                "originPlace": "松脂坳南沟",
                "arrivalKg": "100.00",
                "receivedAt": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ResinLot.objects.filter(lotCode="脂-松脂坳-2409D").exists())
