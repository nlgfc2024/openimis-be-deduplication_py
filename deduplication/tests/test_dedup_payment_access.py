from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from contribution_plan.models import PaymentPlan
from core.test_helpers import (
    create_test_interactive_user,
    create_test_language,
    create_test_role,
)
from deduplication.schema import Query
from deduplication.services import (
    _payment_task_ownership,
    get_benefit_consumption_duplication_aggregation,
)
from individual.models import Individual
from payment_cycle.models import PaymentCycle
from payroll.models import (
    BenefitConsumption,
    BenefitConsumptionStatus,
    Payroll,
    PayrollBenefitConsumption,
    PayrollStatus,
)
from social_protection.apps import SocialProtectionConfig
from social_protection.models import BenefitPlan, UserBenefitPlanAccess


@override_settings(ROW_SECURITY=False)
class PaymentDeduplicationAccessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        create_test_language()
        cls.admin = create_test_interactive_user(username='t09-pay-admin')
        role = create_test_role(
            perm_names=['gql_beneficiary_search_perms'],
            name='Track 09 payment summary',
        )
        cls.upg_user = create_test_interactive_user(
            username='t09-pay-upg', roles=[role.id]
        )
        cls.rmep_user = create_test_interactive_user(
            username='t09-pay-rmep', roles=[role.id]
        )
        cls.both_user = create_test_interactive_user(
            username='t09-pay-both', roles=[role.id]
        )
        cls.none_user = create_test_interactive_user(
            username='t09-pay-none', roles=[role.id]
        )
        cls.all_plan_user = create_test_interactive_user(
            username='t09-pay-all', roles=[role.id]
        )

        cls.upg_plan = cls.create_benefit_plan('T09UPG')
        cls.rmep_plan = cls.create_benefit_plan('T09RMEP')
        for user, benefit_plan in (
            (cls.upg_user, cls.upg_plan),
            (cls.rmep_user, cls.rmep_plan),
            (cls.both_user, cls.upg_plan),
            (cls.both_user, cls.rmep_plan),
        ):
            UserBenefitPlanAccess(
                user=user,
                benefit_plan=benefit_plan,
            ).save(username=cls.admin.username)

        cls.cycle = PaymentCycle(
            code='T09-SHARED-CYCLE',
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            type=ContentType.objects.get_for_model(BenefitPlan),
        )
        cls.cycle.save(username=cls.admin.username)

        upg_payment_plan = cls.create_payment_plan(
            'T09-PP-UPG', benefit_plan=cls.upg_plan,
        )
        rmep_payment_plan = cls.create_payment_plan(
            'T09-PP-RMEP', benefit_plan=cls.rmep_plan,
        )
        legacy_target = cls.create_individual('Legacy target')
        legacy_payment_plan = cls.create_payment_plan(
            'T09-PP-LEGACY',
            benefit_plan_type=ContentType.objects.get_for_model(Individual),
            benefit_plan_id=legacy_target.id,
        )

        cls.upg_payroll = cls.create_payroll('T09 UPG', upg_payment_plan)
        cls.rmep_payroll = cls.create_payroll('T09 RMEP', rmep_payment_plan)
        cls.legacy_payroll = cls.create_payroll(
            'T09 legacy', legacy_payment_plan,
        )

        cls.upg_benefits = cls.create_duplicate_benefits('UPG duplicate', 'UPG')
        cls.rmep_benefits = cls.create_duplicate_benefits('RMEP duplicate', 'RMEP')
        cls.legacy_benefits = cls.create_duplicate_benefits(
            'Legacy duplicate', 'LEGACY',
        )
        cls.cross_benefits = cls.create_duplicate_benefits(
            'Cross duplicate', 'CROSS',
        )

        for benefit in cls.upg_benefits:
            cls.link(cls.upg_payroll, benefit)
        for benefit in cls.rmep_benefits:
            cls.link(cls.rmep_payroll, benefit)
        for benefit in cls.legacy_benefits:
            cls.link(cls.legacy_payroll, benefit)
        for benefit in cls.cross_benefits:
            cls.link(cls.upg_payroll, benefit)
            cls.link(cls.rmep_payroll, benefit)

        # Historical duplicate relation: it must not inflate aggregation counts.
        cls.link(cls.upg_payroll, cls.upg_benefits[0])

    @classmethod
    def create_benefit_plan(cls, code):
        plan = BenefitPlan(code=code, name=code)
        plan.save(username=cls.admin.username)
        return plan

    @classmethod
    def create_payment_plan(
        cls,
        code,
        benefit_plan=None,
        benefit_plan_type=None,
        benefit_plan_id=None,
    ):
        payment_plan_data = {
            'code': code,
            'name': code,
            'calculation': uuid4(),
            'periodicity': 1,
            'benefit_plan_type': benefit_plan_type,
            'benefit_plan_id': (
                str(benefit_plan_id) if benefit_plan_id is not None else None
            ),
        }
        if benefit_plan is not None:
            payment_plan_data['benefit_plan'] = benefit_plan
        payment_plan = PaymentPlan(
            **payment_plan_data,
        )
        payment_plan.save(username=cls.admin.username)
        return payment_plan

    @classmethod
    def create_payroll(cls, name, payment_plan):
        payroll = Payroll(
            name=name,
            payment_plan=payment_plan,
            payment_cycle=cls.cycle,
            status=PayrollStatus.PENDING_APPROVAL,
        )
        payroll.save(username=cls.admin.username)
        return payroll

    @classmethod
    def create_individual(cls, first_name):
        individual = Individual(
            first_name=first_name,
            last_name='Track 09',
            dob=date(1990, 1, 1),
            json_ext={},
        )
        individual.save(username=cls.admin.username)
        return individual

    @classmethod
    def create_duplicate_benefits(cls, first_name, prefix):
        benefits = []
        for index in range(2):
            benefit = BenefitConsumption(
                individual=cls.create_individual(first_name),
                code=f'{prefix}-{index}',
                date_due=date(2026, 1, 15),
                amount=Decimal('100.00'),
                type='CASH',
                status=BenefitConsumptionStatus.ACCEPTED,
            )
            benefit.save(username=cls.admin.username)
            benefits.append(benefit)
        return benefits

    @classmethod
    def link(cls, payroll, benefit):
        link = PayrollBenefitConsumption(payroll=payroll, benefit=benefit)
        link.save(username=cls.admin.username)
        return link

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

    def summary(self, user):
        rows = list(get_benefit_consumption_duplication_aggregation(
            ['first_name'], self.cycle.id, user,
        ))
        return {
            row['individual__first_name']: {
                'count': row['id_count'],
                'ids': set(row['ids']),
            }
            for row in rows
        }

    def test_assignment_matrix_shared_cycle_and_legacy_records(self):
        upg = self.summary(self.upg_user)
        self.assertEqual(set(upg), {'UPG duplicate', 'Legacy duplicate'})
        self.assertEqual(upg['UPG duplicate']['count'], 2)
        self.assertEqual(upg['UPG duplicate']['ids'], {
            benefit.id for benefit in self.upg_benefits
        })
        self.assertEqual(
            _payment_task_ownership(
                [benefit.id for benefit in self.upg_benefits]
            ),
            {'benefit_plan_id': str(self.upg_plan.id)},
        )
        self.assertEqual(
            _payment_task_ownership(
                [benefit.id for benefit in self.legacy_benefits]
            ),
            {'benefit_plan_is_shared': True},
        )

        rmep = self.summary(self.rmep_user)
        self.assertEqual(set(rmep), {'RMEP duplicate', 'Legacy duplicate'})

        both = self.summary(self.both_user)
        self.assertEqual(set(both), {
            'UPG duplicate',
            'RMEP duplicate',
            'Legacy duplicate',
            'Cross duplicate',
        })
        self.assertEqual(both['Cross duplicate']['count'], 2)

        no_assignment = self.summary(self.none_user)
        self.assertEqual(set(no_assignment), {'Legacy duplicate'})

    def test_direct_service_requires_valid_user_context(self):
        with self.assertRaises(PermissionDenied):
            get_benefit_consumption_duplication_aggregation(
                ['first_name'], self.cycle.id,
            )

    def test_superuser_and_all_plan_access(self):
        self.assertEqual(
            set(self.summary(self.admin)),
            {
                'UPG duplicate', 'RMEP duplicate',
                'Legacy duplicate', 'Cross duplicate',
            },
        )

        all_plan_perms = SocialProtectionConfig.gql_benefit_plan_all_access_perms
        with patch.object(
            type(self.all_plan_user),
            'has_perms',
            side_effect=lambda perms: perms == all_plan_perms,
        ):
            summary = self.summary(self.all_plan_user)
        self.assertEqual(set(summary), {
            'UPG duplicate', 'RMEP duplicate',
            'Legacy duplicate', 'Cross duplicate',
        })

    def test_graphql_resolver_does_not_leak_restricted_counts_or_ids(self):
        info = SimpleNamespace(context=SimpleNamespace(user=self.upg_user))
        result = Query().resolve_benefit_deduplication_summary(
            info,
            columns=['first_name'],
            payment_cycle_id=self.cycle.id,
        )
        rows = {row.column_values['individual__first_name']: row for row in result.rows}
        self.assertEqual(set(rows), {'UPG duplicate', 'Legacy duplicate'})
        self.assertNotIn('RMEP duplicate', rows)
        self.assertFalse(
            {benefit.id for benefit in self.rmep_benefits}
            & {value for row in rows.values() for value in row.ids}
        )

    def test_feature_off_preserves_legacy_positional_calls(self):
        SocialProtectionConfig.enable_benefit_plan_access_control = False
        rows = list(get_benefit_consumption_duplication_aggregation(
            ['first_name'], self.cycle.id,
        ))
        self.assertEqual(
            {row['individual__first_name'] for row in rows},
            {
                'UPG duplicate', 'RMEP duplicate',
                'Legacy duplicate', 'Cross duplicate',
            },
        )

    def test_payroll_authorization_denial_is_not_bypassed(self):
        with patch(
            'payroll.benefit_plan_access.filter_payroll_queryset',
            side_effect=PermissionDenied("denied"),
        ), self.assertRaises(PermissionDenied):
            self.summary(self.upg_user)
