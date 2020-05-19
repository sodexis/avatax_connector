from odoo import models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _get_avatax_doc_type(self, commit=False):
        doc_type = super()._get_avatax_doc_type(commit=commit)
        taxes = (
            self.order_line
            .mapped("tax_id")
            .filtered("is_avatax"))
        taxes_expense = taxes.filtered("is_expensed_tax")
        if taxes_expense:
            # if len(taxes_expense) == len(taxes):
            # TODO: else warn can't mix tax types
            doc_type = doc_type.replace("Sales", "Purchase")
        return doc_type


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def _avatax_prepare_line(self, sign=1, doc_type=None):
        res = super()._avatax_prepare_line(sign=sign, doc_type=doc_type)
        if doc_type and "Purchase" in doc_type:
            unit_cost = self.product_id.standard_price
            amount = sign * unit_cost * self.product_uom_qty
            res.update({
                'discounted': False,
                'discount': 0.0,
                'amount': amount,
            })
        return res
