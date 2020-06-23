from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    amount_tax_expense = fields.Monetary(string="Tax Expense", compute="_amount_all")

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

    def _get_avatax_doc_type(self, commit=False):
        doc_type = super()._get_avatax_doc_type(commit=commit)
        taxes = self.order_line.mapped("tax_id").filtered("is_avatax")
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
            for line in self.order_line:
                line._product_margin()
        return True


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    tax_amt_expense = fields.Float("Avalara Tax Expense")
    tax_expense = fields.Monetary(
        string="Tax Expense",
        compute="_compute_amount",
        help="Total tax amount expensed by the company",
    )
    tax_total = fields.Monetary(
        string="Tax",
        compute="_compute_amount",
        help="Total tax amount, both collected from the customer "
        "and expensed by the company.",
    )

    @api.onchange("product_uom_qty", "discount", "price_unit", "tax_id")
    def onchange_reset_avatax_amount(self):
        super().onchange_reset_avatax_amount()
        for line in self:
            line.tax_amt_expense = 0

    def _get_tax_price_unit(self):
        """
        Returns the Base Amount to use for Expensed Tax.
        Use Tax is computed based on purchase price,
        that should be stored in the "purchase_price" field.
        """
        res = super()._get_tax_price_unit()
        if any(x.is_expensed_tax for x in self.tax_id):
            price_unit = self.purchase_price or self.price_unit
            res = price_unit * (1 - (self.discount or 0.0) / 100.0)
        return res

    @api.depends("tax_amt_expense")
    def _compute_amount(self):
        """
        Compute the Tax Expense field
        Needs to be done on the same method computing the tax amount.
        Don't rely on Odoo MRO between Avatax and Expensed Tax
        """
        super()._compute_amount()
        for line in self:
            line.tax_expense = 0
            has_expensed_tax = any(x.is_expensed_tax for x in line.tax_id)
            if has_expensed_tax:
                currency = line.order_id.currency_id or None
                price = line._get_tax_price_unit()
                tax_ids = line.tax_id.with_context(avatax_line=line)
                taxes = tax_ids.compute_all(
                    price,
                    currency,
                    line.product_uom_qty,
                    product=line.product_id,
                    partner=line.order_id.partner_id,
                )
                line.tax_expense = taxes.get("total_expense", 0) if taxes else 0
            line.tax_total = line.price_tax + line.tax_expense
        return

    def _avatax_prepare_line(self, sign=1, doc_type=None):
        res = super()._avatax_prepare_line(sign=sign, doc_type=doc_type)
        if doc_type and "Purchase" in doc_type:
            unit_cost = self._get_tax_price_unit()
            amount = sign * unit_cost * self.product_uom_qty
            res.update(
                {"discounted": False, "discount": 0.0, "amount": amount}
            )
        return res

    def _product_margin(self):
        super()._product_margin()
        for line in self:
            if line.tax_expense:
                line.margin = line.margin - line.tax_expense
