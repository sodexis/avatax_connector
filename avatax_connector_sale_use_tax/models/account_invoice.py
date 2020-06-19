from odoo import fields, models


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

    def _compute_amount(self):
        """
        Compute the Tax Expense field
        Needs to be done on the same method computing the tax amount.
        Don't rely on Odoo MRO between Avatax and Expensed Tax
        """
        super()._compute_amount()
        for inv in self:
            if inv.avatax_amount:
                # TODO: redundant?
                inv.amount_tax_expense = sum(
                    line.tax_expense for line in inv.invoice_line_ids
                )
                inv.amount_tax = 0
                inv.amount_total = inv.amount_untaxed
                inv.amount_total_signed = inv.amount_untaxed_signed

    def _avatax_compute_tax(self, commit=False):
        super()._avatax_compute_tax(commit=commit)
        doc_type = self._get_avatax_doc_type()
        if self.amount_tax_expense and doc_type.startswith("Purchase"):
            for line in self.invoice_line_ids:
                line.tax_amt_expense = line.tax_amt
                line.tax_amt = 0
            # self.tax_amt_expense = 0
        return True


class AccountInvoiceLine(models.Model):
    _inherit = "account.invoice.line"

    tax_amt_expense = fields.Float("Avalara Tax Expense")

    def _compute_price(self):
        """
        Compute the Tax Expense field
        Needs to be done on the same method computing the tax amount.
        Don't rely on Odoo MRO between Avatax and Expensed Tax
        """
        super()._compute_price()
        for line in self:
            if line.tax_amt_expense:
                line.tax_expense = line.tax_amt_expense
                line.price_tax = 0
                line.price_total = line.price_subtotal


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
