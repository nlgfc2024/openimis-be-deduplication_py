from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.db import connection
from deduplication.services import (
    _beneficiary_task_ownership,
    get_beneficiary_duplication_aggregation,
)
from deduplication.tests.data.dedup_beneficiary import benefit_plan_data, individuals_data
from deduplication.tests.helpers import LogInHelper
from deduplication.schema import Query
from core.test_helpers import create_test_interactive_user, create_test_role
from individual.models import Individual
from social_protection.apps import SocialProtectionConfig
from social_protection.models import (
    Beneficiary,
    BenefitPlan,
    BeneficiaryStatus,
    UserBenefitPlanAccess,
)


@override_settings(ROW_SECURITY=False)
class DedupBeneficiaryTestCase(TestCase):
    user = None
    bp = None
    inds = None
    benfs = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = LogInHelper().get_or_create_user_api()
        cls.bp = BenefitPlan(**benefit_plan_data)
        cls.bp.save(username=cls.user.username)
        second_plan_data = {
            **benefit_plan_data,
            'code': 'rmep-code',
            'name': 'RMEP',
        }
        cls.rmep_bp = BenefitPlan(**second_plan_data)
        cls.rmep_bp.save(username=cls.user.username)

        role = create_test_role(
            perm_names=['gql_beneficiary_search_perms'],
            name='Track 09 beneficiary summary',
        )
        cls.upg_user = create_test_interactive_user(
            username='t09-upg', roles=[role.id]
        )
        cls.rmep_user = create_test_interactive_user(
            username='t09-rmep', roles=[role.id]
        )
        cls.both_user = create_test_interactive_user(
            username='t09-both', roles=[role.id]
        )
        cls.none_user = create_test_interactive_user(
            username='t09-none', roles=[role.id]
        )
        cls.all_plan_user = create_test_interactive_user(
            username='t09-all', roles=[role.id]
        )
        for user, benefit_plan in (
            (cls.upg_user, cls.bp),
            (cls.rmep_user, cls.rmep_bp),
            (cls.both_user, cls.bp),
            (cls.both_user, cls.rmep_bp),
        ):
            UserBenefitPlanAccess(
                user=user,
                benefit_plan=benefit_plan,
            ).save(username=cls.user.username)
        cls.inds = list()
        cls.bens = list()

        for i_data in individuals_data:
            i = Individual(**i_data)
            i.save(username=cls.user.username)
            cls.inds.append(i)

        for i in cls.inds:
            b = Beneficiary(
                individual=i,
                benefit_plan=cls.bp,
                status=BeneficiaryStatus.ACTIVE,
                json_ext=i.json_ext
            )
            b.save(username=cls.user.username)
            cls.bens.append(b)

    def setUp(self):
        original_enabled = SocialProtectionConfig.enable_benefit_plan_access_control
        original_all = SocialProtectionConfig.gql_benefit_plan_all_access_perms
        SocialProtectionConfig.enable_benefit_plan_access_control = True
        SocialProtectionConfig.gql_benefit_plan_all_access_perms = ['160006']
        self.addCleanup(
            setattr,
            SocialProtectionConfig,
            'enable_benefit_plan_access_control',
            original_enabled,
        )
        self.addCleanup(
            setattr,
            SocialProtectionConfig,
            'gql_benefit_plan_all_access_perms',
            original_all,
        )

    def test_deduplication_aggregation(self):
        if connection.vendor == 'microsoft':
            self.skipTest("This test can only be executed for PSQL database")
        else:
            res = get_beneficiary_duplication_aggregation(
                ['individual__first_name', 'k1'],
                self.bp.id,
                self.upg_user,
            )
            listed = list(res)
            self.assertEqual(len(listed), 1)
            response = listed[0]
            self.assertEqual(response['id_count'], 2)
            self.assertEqual(response['individual__first_name'], 'first name 1')

    def test_direct_service_enforces_benefit_plan_assignments(self):
        allowed = list(get_beneficiary_duplication_aggregation(
            ['individual__first_name'], self.bp.id, self.upg_user,
        ))
        self.assertEqual(allowed[0]['id_count'], 2)

        self.assertEqual(
            _beneficiary_task_ownership([beneficiary.id for beneficiary in self.bens]),
            {'benefit_plan_id': str(self.bp.id)},
        )

        with self.assertRaises(PermissionDenied):
            get_beneficiary_duplication_aggregation(
                ['individual__first_name'], self.bp.id, self.rmep_user,
            )
        with self.assertRaises(PermissionDenied):
            get_beneficiary_duplication_aggregation(
                ['individual__first_name'], self.bp.id, self.none_user,
            )
        with self.assertRaises(PermissionDenied):
            get_beneficiary_duplication_aggregation(
                ['individual__first_name'], self.bp.id,
            )

        self.assertEqual(
            list(get_beneficiary_duplication_aggregation(
                ['individual__first_name'], self.rmep_bp.id, self.both_user,
            )),
            [],
        )

    def test_all_plan_and_superuser_access(self):
        all_plan_perms = SocialProtectionConfig.gql_benefit_plan_all_access_perms
        with patch.object(
            type(self.all_plan_user),
            'has_perms',
            side_effect=lambda perms: perms == all_plan_perms,
        ):
            result = list(get_beneficiary_duplication_aggregation(
                ['individual__first_name'], self.bp.id, self.all_plan_user,
            ))
        self.assertEqual(result[0]['id_count'], 2)

        result = list(get_beneficiary_duplication_aggregation(
            ['individual__first_name'], self.bp.id, self.user,
        ))
        self.assertEqual(result[0]['id_count'], 2)

    def test_graphql_resolver_denies_counts_ids_and_values(self):
        info = SimpleNamespace(
            context=SimpleNamespace(user=self.upg_user),
        )
        result = Query().resolve_beneficiary_deduplication_summary(
            info,
            columns=['first_name'],
            benefit_plan_id=self.bp.id,
        )
        self.assertEqual(result.rows[0].count, 2)
        self.assertEqual(len(result.rows[0].ids), 2)
        self.assertEqual(
            result.rows[0].column_values['individual__first_name'],
            'first name 1',
        )

        unauthorized_info = SimpleNamespace(
            context=SimpleNamespace(user=self.rmep_user),
        )
        with self.assertRaises(PermissionDenied):
            Query().resolve_beneficiary_deduplication_summary(
                unauthorized_info,
                columns=['first_name'],
                benefit_plan_id=self.bp.id,
            )

    def test_feature_off_preserves_legacy_missing_user_calls(self):
        SocialProtectionConfig.enable_benefit_plan_access_control = False
        result = list(get_beneficiary_duplication_aggregation(
            ['individual__first_name'], self.bp.id,
        ))
        self.assertEqual(result[0]['id_count'], 2)

    def test_enabled_mode_propagates_shared_authorization_denial(self):
        with patch(
            'deduplication.services.require_benefit_plan_access',
            side_effect=PermissionDenied("denied"),
        ), self.assertRaises(PermissionDenied):
            get_beneficiary_duplication_aggregation(
                ['individual__first_name'], self.bp.id, self.upg_user,
            )
