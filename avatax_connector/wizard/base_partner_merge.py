from odoo import models


class MergePartnerAutomatic(models.TransientModel):
    _inherit = 'base.partner.merge.automatic.wizard'

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        """
        Merging Partner records errors
        when the target Partner has no Avatax Customer Code.
        The merge operation copies the source's Customer Code to the target,
        and this violates the unique constraint.
        To avoid this, ensure the target Partner has an Avatax Customer Code.
        """
        if not dst_partner.customer_code:
            dst_partner.generate_cust_code()
        return super()._merge(partner_ids, dst_partner, extra_checks)
