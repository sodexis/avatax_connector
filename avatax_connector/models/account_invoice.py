import logging
import time
from odoo import api, fields, models, _
import odoo.addons.decimal_precision as dp
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class AccountInvoice(models.Model):
    """Inherit to implement the tax calculation using avatax API"""

    _inherit = "account.invoice"

    @api.onchange("partner_id", "company_id")
    def _onchange_partner_id(self):
        res = super(AccountInvoice, self)._onchange_partner_id()
        if not self.exemption_locked:
            self.exemption_code = self.partner_id.exemption_number or ""
            self.exemption_code_id = self.partner_id.exemption_code_id.id or None
        if self.partner_id.validation_method:
            self.is_add_validate = True
        else:
            self.is_add_validate = False
        return res

    @api.onchange("warehouse_id")
    def onchange_warehouse_id(self):
        if self.warehouse_id:
            if self.warehouse_id.company_id:
                self.company_id = self.warehouse_id.company_id
            if self.warehouse_id.code:
                self.location_code = self.warehouse_id.code

    invoice_doc_no = fields.Char(
        "Source/Ref Invoice No",
        readonly=True,
        states={"draft": [("readonly", False)]},
        help="Reference of the invoice",
    )
    invoice_date = fields.Date("Tax Invoice Date", readonly=True)
    is_add_validate = fields.Boolean("Address Is Validated")
    exemption_code = fields.Char(
        "Exemption Number", help="It show the customer exemption number"
    )
    exemption_code_id = fields.Many2one(
        "exemption.code", "Exemption Code", help="It show the customer exemption code"
    )
    exemption_locked = fields.Boolean(
        help="Exemption code won't be automatically changed, "
        "for instance, when changing the Customer."
    )
    tax_on_shipping_address = fields.Boolean(
        "Tax based on shipping address", default=True
    )
    shipping_add_id = fields.Many2one(
        "res.partner", "Tax Shipping Address", compute="_compute_shipping_add_id"
    )
    shipping_address = fields.Text("Tax Shipping Address Text")
    location_code = fields.Char(
        "Location Code", readonly=True, states={"draft": [("readonly", False)]}
    )
    warehouse_id = fields.Many2one("stock.warehouse", "Warehouse")
    disable_tax_calculation = fields.Boolean("Disable Avatax Tax calculation")
    avatax_amount = fields.Float(
        "Tax Code Amount",
        digits=dp.get_precision("Sale Price")
    )

    @api.depends(
        'invoice_line_ids.price_subtotal', 'tax_line_ids.amount',
        'tax_line_ids.amount_rounding', 'currency_id', 'company_id',
        'date_invoice', 'type',
        'avatax_amount'
    )
    def _compute_amount(self):
        super()._compute_amount()
        for inv in self:
            if inv.avatax_amount:
                inv.amount_tax = inv.avatax_amount
                inv.amount_total = inv.amount_untaxed + inv.amount_tax
                sign = inv.type in ['in_refund', 'out_refund'] and -1 or 1
                inv.amount_total_signed = inv.amount_total * sign

    @api.multi
    @api.depends("tax_on_shipping_address", "partner_id", "partner_shipping_id")
    def _compute_shipping_add_id(self):
        for invoice in self:
            invoice.shipping_add_id = (
                invoice.partner_shipping_id
                if invoice.tax_on_shipping_address
                else invoice.partner_id
            )

    @api.onchange('invoice_line_ids', 'shipping_add_id', 'fiscal_position_id')
    def onchange_reset_avatax_amount(self):
        """
        When changing quantities or prices, reset the Avatax computed amount.
        The Odoo computed tax amount will then be shown, as a reference.
        The Avatax amount will be recomputed upon document validation.
        """
        for inv in self:
            inv.avatax_amount = 0
            inv.invoice_line_ids.write({"tax_amt": 0})

    @api.multi
    def get_origin_tax_date(self):
        for inv_obj in self:
            if inv_obj.origin:
                a = inv_obj.origin

                if len(a.split(":")) > 1:
                    inv_origin = a.split(":")[1]
                else:
                    inv_origin = a.split(":")[0]

                inv_ids = self.search([("number", "=", inv_origin)])
                for invoice in inv_ids:
                    if invoice.date_invoice:
                        return invoice.date_invoice
                    else:
                        return inv_obj.date_invoice
        # else:
        return False

    def _get_avatax_doc_type(self, commit=False):
        self.ensure_one()
        if not commit:
            doc_type = "SalesOrder"
        elif self.type == "out_refund":
            doc_type = "ReturnInvoice"
        else:
            doc_type = "SalesInvoice"
        return doc_type

    def _avatax_prepare_lines(self, doc_type=None):
        """
        Prepare the lines to use for Avatax computation.
        Returns a list of dicts
        """
        sign = self.type == "out_invoice" and 1 or -1
        lines = [
            line._avatax_prepare_line(sign, doc_type) for line in self.invoice_line_ids
            if line.price_subtotal
        ]
        return lines

    def _avatax_compute_tax(self, commit=False):
        """ Contact REST API and recompute taxes for a Sale Order """
        self and self.ensure_one()
        Tax = self.env["account.tax"]
        avatax_config = self.company_id.get_avatax_config_company()
        commit = commit and not avatax_config.disable_tax_reporting
        doc_type = self._get_avatax_doc_type(commit)
        tax_date = self.get_origin_tax_date() or self.date_invoice
        taxable_lines = self._avatax_prepare_lines(doc_type)
        tax_result = avatax_config.create_transaction(
            self.date_invoice or fields.Date.today(),
            self.number,
            doc_type,
            self.partner_id,
            self.warehouse_id.partner_id or self.company_id.partner_id,
            self.partner_shipping_id or self.partner_id,
            taxable_lines,
            self.user_id,
            self.exemption_code or None,
            self.exemption_code_id.code or None,
            commit,
            tax_date,
            self.invoice_doc_no,
            self.location_code or "",
            is_override=self.type == "out_refund",
            currency_id=self.currency_id,
            ignore_error=300 if commit else None,
        )
        # If commiting, and document exists, try unvoiding it
        # Error number 300 = GetTaxError, Expected Saved|Posted
        if commit and tax_result.get("number") == 300:
            _logger.info(
                "Document %s (%s) already exists in Avatax. "
                "Should be a voided transaction. "
                "Unvoiding and re-commiting.",
                self.number,
                doc_type
            )
            avatax_config.unvoid_transaction(self.number, doc_type)
            avatax_config.commit_transaction(self.number, doc_type)
            return True

        tax_result_lines = {int(x["lineNumber"]): x for x in tax_result["lines"]}
        for line in self.invoice_line_ids:
            tax_result_line = tax_result_lines.get(line.id)
            if tax_result_line:
                rate = tax_result_line.get("rate", 0.0)
                tax = Tax.get_avalara_tax(rate, doc_type)
                if tax not in line.invoice_line_tax_ids:
                    line_taxes = line.invoice_line_tax_ids.filtered(
                        lambda x: not x.is_avatax or x.amount == 0
                    )
                    line.invoice_line_tax_ids = line_taxes | tax
                line.tax_amt = tax_result_line["tax"]
        self.avatax_amount = tax_result["totalTax"]
        return True

    @api.multi
    def avatax_compute_taxes(self, commit_avatax=False):
        """
        Called from Invoice's Action menu.
        Forces computation of the Invoice taxes
        """
        for invoice in self:
            # The onchange invoice lines call get_taxes_values()
            # and applies it to the invoice's tax_line_ids
            # invoice.with_context(contact_avatax=True)._onchange_invoice_line_ids()
            has_avatax_tax = invoice.mapped(
                "invoice_line_ids.invoice_line_tax_ids.is_avatax"
            )
            if has_avatax_tax:
                avatax_config = self.company_id.get_avatax_config_company()
                if avatax_config:
                    if "rest" in avatax_config.service_url:
                        invoice._avatax_compute_tax(commit=commit_avatax)
                        invoice._onchange_invoice_line_ids()
                    else:
                        taxes_grouped = invoice.get_taxes_values(
                            contact_avatax=True, commit_avatax=commit_avatax
                        )
                        tax_lines = invoice.tax_line_ids.filtered("manual")
                        for tax in taxes_grouped.values():
                            tax_lines += tax_lines.new(tax)
                        invoice.tax_line_ids = tax_lines
        return True

    @api.multi
    def action_invoice_open(self):
        avatax_config = self.company_id.get_avatax_config_company()
        if avatax_config and avatax_config.force_address_validation:
            for addr in [self.partner_id, self.partner_shipping_id]:
                if not addr.date_validation:
                    # The Validate action will be interrupted
                    # if the address is not validated
                    return addr.button_avatax_validate_address()
        # We should compute taxes before validating the invoice
        # , to ensure correct account moves
        # We can only commit to Avatax after validating the invoice
        # , because we need the generated Invoice number
        self.avatax_compute_taxes(commit_avatax=False)
        super(AccountInvoice, self).action_invoice_open()
        self.avatax_compute_taxes(commit_avatax=True)
        return True

    @api.multi
    def get_taxes_values(self, contact_avatax=False, commit_avatax=False):
        """
        Extends the standard method reponsible for computing taxes.
        Returns a dict with the taxes values, ready to be use to create tax_line_ids.
        Used for SOAP API only.
        """
        avatax_config = self.company_id.get_avatax_config_company()
        account_tax_obj = self.env["account.tax"]
        tax_grouped = {}
        # avatax charges customers per API call, so don't hit their API in every onchange, only when saving
        contact_avatax = (
            contact_avatax
            or self.env.context.get("contact_avatax")
            or avatax_config.enable_immediate_calculation
        )
        has_avatax = any(x.tax_id.is_avatax for x in self.tax_line_ids)
        # TODO don't override get_taxes_values()
        if contact_avatax and self.type in ["out_invoice", "out_refund"] and has_avatax:
            avatax_id = account_tax_obj.search(
                [
                    ("is_avatax", "=", True),
                    ("type_tax_use", "in", ["sale", "all"]),
                    ("company_id", "=", self.company_id.id),
                ],
                limit=1
            )
            if not avatax_id:
                raise UserError(
                    _(
                        'Please configure tax information in "AVATAX" settings.  '
                        "The documentation will assist you in proper configuration "
                        "of all the tax code settings as well as "
                        "how they relate to the product. "
                        "\n\n Accounting->Configuration->Taxes->Taxes"
                    )
                )

            tax_date = self.get_origin_tax_date() or self.date_invoice

            sign = self.type == "out_invoice" and 1 or -1
            lines = self._avatax_prepare_lines()
            if lines:
                ship_from_address_id = (
                    self.warehouse_id.partner_id or self.company_id.partner_id
                )
                o_tax_amt = 0.0
                tax = avatax_id

                commit = commit_avatax and not avatax_config.disable_tax_reporting
                if commit:
                    doc_type = (
                        "ReturnInvoice" if self.invoice_doc_no else "SalesInvoice"
                    )
                else:
                    doc_type = "SalesOrder"

                tax_result = account_tax_obj._get_compute_tax(  # SOAP
                    avatax_config,
                    self.date_invoice or time.strftime("%Y-%m-%d"),
                    self.number,
                    doc_type,  # 'SalesOrder',
                    self.partner_id,
                    ship_from_address_id,
                    self.shipping_add_id,
                    lines,
                    self.user_id,
                    self.exemption_code or None,
                    self.exemption_code_id.code or None,
                    commit,
                    tax_date,
                    self.invoice_doc_no,
                    self.location_code or "",
                    is_override=self.type == "out_refund",
                    currency_id=self.currency_id,
                )
                o_tax = tax_result.TotalTax

                if o_tax:
                    val = {
                        "invoice_id": self.id,
                        "name": tax[0].name,
                        "tax_id": tax[0].id,
                        "amount": float(o_tax) * sign,
                        "base": 0,  # float(o_tax.TotalTaxable),
                        "manual": False,
                        "sequence": tax[0].sequence,
                        "account_analytic_id": tax[0].analytic
                        and lines[0]["account_analytic_id"]
                        or False,
                        "analytic_tag_ids": lines[0]["analytic_tag_ids"] or False,
                        "account_id": (
                            self.type in ("out_invoice", "in_invoice")
                            and (tax[0].account_id.id or lines[0]["account_id"])
                            or (tax[0].refund_account_id.id or lines[0]["account_id"])
                        ),
                    }
                    if (
                        not val.get("account_analytic_id")
                        and lines[0]["account_analytic_id"]
                        and val["account_id"] == lines[0]["account_id"]
                    ):
                        val["account_analytic_id"] = lines[0]["account_analytic_id"]

                    key = avatax_id.get_grouping_key(val)
                    if key not in tax_grouped:
                        tax_grouped[key] = val
                    else:
                        tax_grouped[key]["amount"] += val["amount"]
                        tax_grouped[key]["base"] += val["base"]

            for line in self.invoice_line_ids:
                price_unit = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
                taxes = line.invoice_line_tax_ids.compute_all(
                    price_unit,
                    self.currency_id,
                    line.quantity,
                    line.product_id,
                    self.partner_id,
                )["taxes"]
                for tax in taxes:
                    val = {
                        "invoice_id": self.id,
                        "name": tax["name"],
                        "tax_id": tax["id"],
                        "amount": tax["amount"],
                        "base": tax["base"],
                        "manual": False,
                        "sequence": tax["sequence"],
                        "account_analytic_id": tax["analytic"]
                        and line.account_analytic_id.id
                        or False,
                        "analytic_tag_ids": line.analytic_tag_ids.ids or False,
                        "account_id": self.type in ("out_invoice", "in_invoice")
                        and (tax["account_id"] or line.account_id.id)
                        or (tax["refund_account_id"] or line.account_id.id),
                    }

                    # If the taxes generate moves on the same financial account as the invoice line,
                    # propagate the analytic account from the invoice line to the tax line.
                    # This is necessary in situations were (part of) the taxes cannot be reclaimed,
                    # to ensure the tax move is allocated to the proper analytic account.
                    if (
                        not val.get("account_analytic_id")
                        and line.account_analytic_id
                        and val["account_id"] == line.account_id.id
                    ):
                        val["account_analytic_id"] = line.account_analytic_id.id

                    key = avatax_id.get_grouping_key(val)
                    if key not in tax_grouped:
                        tax_grouped[key] = val
                    else:
                        tax_grouped[key]["amount"] += val["amount"]
                        tax_grouped[key]["base"] += val["base"]
            return tax_grouped
        else:
            tax_grouped = super(AccountInvoice, self).get_taxes_values()
        return tax_grouped

    @api.model
    def _prepare_refund(
        self, invoice, date_invoice=None, date=None, description=None, journal_id=None
    ):
        values = super(AccountInvoice, self)._prepare_refund(
            invoice,
            date_invoice=date_invoice,
            date=date,
            description=description,
            journal_id=journal_id,
        )
        values.update(
            {
                "invoice_doc_no": invoice.number,
                "invoice_date": invoice.date_invoice,
                "tax_on_shipping_address": invoice.tax_on_shipping_address,
                "warehouse_id": invoice.warehouse_id.id,
                "location_code": invoice.location_code,
                "exemption_code": invoice.exemption_code or "",
                "exemption_code_id": invoice.exemption_code_id.id or None,
                "shipping_add_id": invoice.shipping_add_id.id,
            }
        )
        return values

    @api.multi
    def action_cancel(self):
        for invoice in self:
            avatax_config = invoice.company_id.get_avatax_config_company()
            has_avatax_tax = invoice.mapped(
                "invoice_line_ids.invoice_line_tax_ids.is_avatax"
            )
            if (
                invoice.type in ["out_invoice", "out_refund"]
                and has_avatax_tax
                and invoice.partner_id.country_id in avatax_config.country_ids
                and invoice.state != "draft"
            ):
                doc_type = invoice._get_avatax_doc_type(commit=True)
                avatax_config.void_transaction(invoice.number, doc_type)
        return super(AccountInvoice, self).action_cancel()


class AccountInvoiceLine(models.Model):
    _inherit = "account.invoice.line"

    tax_amt = fields.Float("Avalara Tax", help="Tax computed by Avalara",)

    @api.onchange("product_id")
    def _onchange_product_id(self):
        avatax_config = self.invoice_id.company_id.get_avatax_config_company()
        if not avatax_config.disable_tax_calculation:
            if self.invoice_id.type in ("out_invoice", "out_refund"):
                taxes = self.product_id.taxes_id or self.account_id.tax_ids
            else:
                taxes = self.product_id.supplier_taxes_id or self.account_id.tax_ids

            if not all(taxes.mapped("is_avatax")):
                warning = {
                    "title": _("Warning!"),
                    "message": _("All used taxes must be configured to use Avatax!"),
                }
                return {"warning": warning}
        return super(AccountInvoiceLine, self)._onchange_product_id()

    def _avatax_prepare_line(self, sign=1, doc_type=None):
        """
        Prepare a line to use for Avatax computation.
        Returns a dict
        """
        line = self
        res = {}
        if line.invoice_line_tax_ids.filtered("is_avatax"):
            # Add UPC to product item code
            avatax_config = line.company_id.get_avatax_config_company()
            if line.product_id.barcode and avatax_config.upc_enable:
                item_code = "upc:" + line.product_id.barcode
            else:
                item_code = line.product_id.default_code
            tax_code = (
                line.product_id.tax_code_id.name
                or line.product_id.categ_id.tax_code_id.name
            )
            amount = (
                sign * line.price_unit * line.quantity * (1 - line.discount / 100.0)
            )
            # Calculate discount amount
            discount_amount = 0.0
            is_discounted = False
            if line.discount:
                discount_amount = (
                    sign * line.price_unit * line.quantity * line.discount / 100.0
                )
                is_discounted = True
            res = {
                "qty": line.quantity,
                "itemcode": line.product_id and item_code or None,
                "description": line.name,
                "discounted": is_discounted,
                "discount": discount_amount,
                "amount": amount,
                "tax_code": tax_code,
                "id": line,
                "account_analytic_id": line.account_analytic_id.id,
                "analytic_tag_ids": line.analytic_tag_ids.ids,
                "account_id": line.account_id.id,
                "tax_id": line.invoice_line_tax_ids,
            }
        return res

    @api.depends(
        'price_unit', 'discount', 'invoice_line_tax_ids', 'quantity',
        'product_id', 'invoice_id.partner_id', 'invoice_id.currency_id',
        'invoice_id.company_id', 'invoice_id.date_invoice', 'invoice_id.date',
        'tax_amt')
    def _compute_price(self):
        super()._compute_price()
        for line in self:
            if line.tax_amt:
                line.price_total = line.price_subtotal + line.tax_amt

    @api.onchange('price_unit', 'discount', 'invoice_line_tax_ids', 'quantity')
    def onchange_reset_avatax_amount(self):
        """
        When changing quantities or prices, reset the Avatax computed amount.
        The Odoo computed tax amount will then be shown, as a reference.
        The Avatax amount will be recomputed upon document validation.
        """
        for line in self:
            line.tax_amt = 0
