from odoo import models


class AccountInvoice(models.Model):
    _inherit = "account.invoice"

    def _get_avatax_doc_type(self, commit=False):
        doc_type = super()._get_avatax_doc_type(commit=commit)
        taxes = (
            self.invoice_line_ids
            .mapped("invoice_line_tax_ids")
            .filtered("is_avatax"))
        taxes_expense = taxes.filtered("is_expensed_tax")
        if taxes_expense:
            # if len(taxes_expense) == len(taxes):
            # TODO: else warn can't mix tax types
            doc_type = doc_type.replace("Sales", "Purchase")
        return doc_type


class AccountInvoiceLine(models.Model):
    _inherit = "account.invoice.line"

    def _avatax_prepare_line(self, sign=1, doc_type=None):
        res = super()._avatax_prepare_line(sign=sign, doc_type=doc_type)
        if doc_type and "Purchase" in doc_type:
            unit_cost = self.product_id.standard_price
            amount = sign * unit_cost * self.quantity
            res.update({
                'discounted': False,
                'discount': 0.0,
                'amount': amount,
            })
        return res
