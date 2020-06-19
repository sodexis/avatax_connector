from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.depends("order_line.tax_expense")
    def _amount_all(self):
        """
        Compute Expensed Tax
        Correct the previously computed tax, deducting the expensed tax
        Don't rely on Odoo MRO between Avatax and Expensed Tax
        """
        super()._amount_all()
        for order in self:
            order.amount_tax_expense = sum(
                line.tax_expense for line in order.order_line
            )
            order.amount_tax -= order.amount_tax_expense
            order.amount_total -= order.amount_tax_expense

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

    def _avatax_compute_tax(self):
        super()._avatax_compute_tax()
        doc_type = self._get_avatax_doc_type()
        if self.tax_amount and doc_type.startswith("Purchase"):
            for line in self.order_line:
                line.tax_amt_expense = line.tax_amt
                line.tax_amt = 0
            self.tax_amount = 0
        return True


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    tax_amt_expense = fields.Float("Avalara Tax Expense")

    @api.depends("tax_amt_expense")
    def _compute_amount(self):
        """
        Compute the Tax Expense field
        Needs to be done on the same method computing the tax amount.
        Don't rely on Odoo MRO between Avatax and Expensed Tax
        """
        super()._compute_amount()
        for line in self:
            if line.tax_amt_expense:
                line.tax_expense = line.tax_amt_expense
                line.price_tax = 0
                line.price_total = line.price_subtotal

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
