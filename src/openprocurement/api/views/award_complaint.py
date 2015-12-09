# -*- coding: utf-8 -*-
from logging import getLogger
from openprocurement.api.models import STAND_STILL_TIME, get_now
from openprocurement.api.utils import (
    add_next_award,
    apply_patch,
    check_tender_status,
    context_unpack,
    json_view,
    opresource,
    save_tender,
    set_ownership,
)
from openprocurement.api.validation import (
    validate_complaint_data,
    validate_patch_complaint_data,
)


LOGGER = getLogger(__name__)


@opresource(name='Tender Award Complaints',
            collection_path='/tenders/{tender_id}/awards/{award_id}/complaints',
            path='/tenders/{tender_id}/awards/{award_id}/complaints/{complaint_id}',
            procurementMethodType='belowThreshold',
            description="Tender award complaints")
class TenderAwardComplaintResource(object):

    def __init__(self, request, context):
        self.context = context
        self.request = request
        self.db = request.registry.db

    @json_view(content_type="application/json", permission='create_award_complaint', validators=(validate_complaint_data,))
    def collection_post(self):
        """Post a complaint for award
        """
        tender = self.request.validated['tender']
        if tender.status not in ['active.qualification', 'active.awarded']:
            self.request.errors.add('body', 'data', 'Can\'t add complaint in current ({}) tender status'.format(tender.status))
            self.request.errors.status = 403
            return
        if any([i.status != 'active' for i in tender.lots if i.id == self.context.lotID]):
            self.request.errors.add('body', 'data', 'Can add complaint only in active lot status')
            self.request.errors.status = 403
            return
        if self.context.complaintPeriod and \
           (self.context.complaintPeriod.startDate and self.context.complaintPeriod.startDate > get_now() or
                self.context.complaintPeriod.endDate and self.context.complaintPeriod.endDate < get_now()):
            self.request.errors.add('body', 'data', 'Can add complaint only in complaintPeriod')
            self.request.errors.status = 403
            return
        complaint = self.request.validated['complaint']
        set_ownership(complaint, self.request)
        self.context.complaints.append(complaint)
        if save_tender(self.request):
            LOGGER.info('Created tender award complaint {}'.format(complaint.id),
                        extra=context_unpack(self.request, {'MESSAGE_ID': 'tender_award_complaint_create'}, {'complaint_id': complaint.id}))
            self.request.response.status = 201
            self.request.response.headers['Location'] = self.request.route_url('Tender Award Complaints', tender_id=tender.id, award_id=self.request.validated['award_id'], complaint_id=complaint['id'])
            return {
                'data': complaint.serialize("view"),
                'access': {
                    'token': complaint.owner_token
                }
            }

    @json_view(permission='view_tender')
    def collection_get(self):
        """List complaints for award
        """
        return {'data': [i.serialize("view") for i in self.context.complaints]}

    @json_view(permission='view_tender')
    def get(self):
        """Retrieving the complaint for award
        """
        return {'data': self.context.serialize("view")}

    @json_view(content_type="application/json", permission='review_complaint', validators=(validate_patch_complaint_data,))
    def patch(self):
        """Post a complaint resolution for award
        """
        tender = self.request.validated['tender']
        if tender.status not in ['active.qualification', 'active.awarded']:
            self.request.errors.add('body', 'data', 'Can\'t update complaint in current ({}) tender status'.format(tender.status))
            self.request.errors.status = 403
            return
        if any([i.status != 'active' for i in tender.lots if i.id == self.request.validated['award'].lotID]):
            self.request.errors.add('body', 'data', 'Can update complaint only in active lot status')
            self.request.errors.status = 403
            return
        complaint = self.context
        if complaint.status != 'draft':
            self.request.errors.add('body', 'data', 'Can\'t update complaint in current ({}) status'.format(complaint.status))
            self.request.errors.status = 403
            return
        if self.request.validated['data'].get('status', complaint.status) == 'cancelled':
            self.request.errors.add('body', 'data', 'Can\'t cancel complaint')
            self.request.errors.status = 403
            return
        apply_patch(self.request, save=False, src=complaint.serialize())
        if complaint.status == 'resolved':
            award = self.request.validated['award']
            if tender.status == 'active.awarded':
                tender.status = 'active.qualification'
                tender.awardPeriod.endDate = None
            now = get_now()
            if award.status == 'unsuccessful':
                for i in tender.awards[tender.awards.index(award):]:
                    if i.lotID != award.lotID:
                        continue
                    i.complaintPeriod.endDate = now + STAND_STILL_TIME
                    i.status = 'cancelled'
                    for j in i.complaints:
                        if j.status == 'draft':
                            j.status = 'cancelled'
            for i in tender.contracts:
                if award.id == i.awardID:
                    i.status = 'cancelled'
            award.complaintPeriod.endDate = now + STAND_STILL_TIME
            award.status = 'cancelled'
            add_next_award(self.request)
        elif complaint.status in ['declined', 'invalid'] and tender.status == 'active.awarded':
            check_tender_status(self.request)
        if save_tender(self.request):
            LOGGER.info('Updated tender award complaint {}'.format(self.context.id),
                        extra=context_unpack(self.request, {'MESSAGE_ID': 'tender_award_complaint_patch'}))
            return {'data': complaint.serialize("view")}
