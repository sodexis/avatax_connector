from odoo import api, fields, models, _
import odoo.addons.decimal_precision as dp
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.onchange("partner_shipping_id", "partner_id", "company_id")
    def onchange_partner_shipping_id(self):
        """
        Apply the exemption number and code from the Invoice Partner Data
        We can only apply an exemption status that matches the delivery
        address Country and State.

        The setup for this is to add contact/addresses for the Invoicing Partner,
        for each of the states we can claim exemption for.
        """
        res = super(SaleOrder, self).onchange_partner_shipping_id()
        self.tax_on_shipping_address = bool(self.partner_shipping_id)
        self.is_add_validate = bool(self.partner_shipping_id.validation_method)
        return res

    @api.depends("tax_add_id", "partner_invoice_id", "partner_id", "company_id")
    def _compute_onchange_exemption(self):
        for order in self:
            # Find an exemption address matching the Country + State
            # of the Delivery address
            invoice_partner = order.partner_invoice_id.commercial_partner_id
            ship_to_address = order.tax_add_id
            exemption_addresses = (
                invoice_partner | invoice_partner.child_ids
            ).filtered("property_tax_exempt")
            exemption_address_naive = exemption_addresses.filtered(
                lambda a: a.country_id == ship_to_address.country_id
                and (
                    a.state_id == ship_to_address.state_id
                    or invoice_partner.property_exemption_country_wide
                )
            )[:1]
            # Force Company to get the correct values form the Property fields
            exemption_address = exemption_address_naive.with_context(
                force_company=order.company_id.id
            )
            order.exemption_code = exemption_address.property_exemption_number
            order.exemption_code_id = exemption_address.property_exemption_code_id

    @api.multi
    def _prepare_invoice(self):
        invoice_vals = super(SaleOrder, self)._prepare_invoice()
        invoice_vals.update(
            {
                "location_code": self.location_code or "",
                "warehouse_id": self.warehouse_id.id or "",
                "tax_on_shipping_address": self.tax_on_shipping_address,
            }
        )
        return invoice_vals

    def action_invoice_create(self, grouped=False, final=False):
        """
        Computed writeable fields cannot be set values on create.
        So, setting the SO exemprion code on the Invoice
        does not work on _prepare_invoice(),
        and is written here, after invoice creation.
        """
        invoice_ids = super().action_invoice_create(grouped=grouped, final=final)
        invoices = self.env["account.invoice"].browse(invoice_ids)
        for order in self:
            if order.exemption_code_id or order.exemption_code:
                order_new_invoices = invoices & order.invoice_ids
                order_new_invoices.write({
                    "exemption_code": order.exemption_code or "",
                    "exemption_code_id": order.exemption_code_id.id or False,
                    "exemption_locked": True,
                })
        return invoice_ids

    @api.depends("order_line.price_total", "order_line.tax_amt", "order_line.tax_id")
    def _amount_all(self):
        """
        Compute fields amount_untaxed, amount_tax, amount_total
        Their computation needs to be overriden,
        to use the amounts returned by Avatax service, stored in specific fields.
        """
        super()._amount_all()
        for order in self:
            has_avatax_tax = order.mapped("order_line.tax_id.is_avatax")
            # Only use Avatax computed amount if there is one
            # Otherwise use the doo computed one
            if has_avatax_tax and order.tax_amount:
                order.update(
                    {
                        "amount_tax": order.tax_amount,
                        "amount_total": order.amount_untaxed + order.tax_amount,
                    }
                )

    @api.depends("tax_on_shipping_address", "partner_id", "partner_shipping_id")
    def _compute_tax_add_id(self):
        """
        SOAP API only
        """
        for invoice in self:
            invoice.tax_add_id = (
                invoice.partner_shipping_id
                if invoice.tax_on_shipping_address
                else invoice.partner_id
            )

    exemption_code = fields.Char(
        "Exemption Number",
        compute=_compute_onchange_exemption,
        readonly=False,  # New computed writeable fields
        store=True,
        help="It show the customer exemption number",
    )
    is_add_validate = fields.Boolean("Address Is validated")
    exemption_code_id = fields.Many2one(
        "exemption.code",
        "Exemption Code",
        compute=_compute_onchange_exemption,
        readonly=False,  # New computed writeable fields
        store=True,
        help="It show the customer exemption code",
    )
    amount_untaxed = fields.Monetary(
        string="Untaxed Amount",
        store=True,
        readonly=True,
        compute="_amount_all",
        track_visibility="always",
    )
    amount_tax = fields.Monetary(
        string="Taxes",
        store=True,
        readonly=True,
        compute="_amount_all",
        track_visibility="always",
    )
    amount_total = fields.Monetary(
        string="Total",
        store=True,
        readonly=True,
        compute="_amount_all",
        track_visibility="always",
    )
    tax_amount = fields.Float("AvaTax Amount", digits=dp.get_precision("Sale Price"))
    tax_on_shipping_address = fields.Boolean(
        "Tax based on shipping address", default=True
    )
    tax_add_id = fields.Many2one(
        "res.partner",
        "Tax Address",
        readonly=True,
        states={"draft": [("readonly", False)]},
        compute="_compute_tax_add_id",
        store=True,
    )
    tax_address = fields.Text("Tax Address Text")
    location_code = fields.Char("Location Code", help="Origin address location code")

    @api.onchange("order_line", "fiscal_position_id", "partner_shipping_id")
    def onchange_reset_avatax_amount(self):
        """
        When changing quantities or prices, reset the Avatax computed amount.
        The Odoo computed tax amount will then be shown, as a reference.
        The Avatax amount will be recomputed upon document validation.
        """
        for order in self:
            order.tax_amount = 0
            for line in order.order_line:
                line.tax_amt = 0

    def _get_avatax_doc_type(self, commit=False):
        return "SalesOrder"

    def _avatax_prepare_lines(self, doc_type=None):
        """
        Prepare the lines to use for Avatax computation.
        Returns a list of dicts
        """
        lines = [
            line._avatax_prepare_line(sign=1, doc_type=doc_type)
            for line in self.order_line
            if line.price_unit or line.product_uom_qty
        ]
        return [x for x in lines if x]

    def compute_tax(self):
        """ Create and update tax amount for each and every order line and shipping line.
        @param order_line: send sub_total of each line and get tax amount
        @param shiping_line: send shipping amount of each ship line and get ship tax amount

        SOAP API only
        """
        if self.env.context.get("doing_compute_tax"):
            return False
        self = self.with_context(doing_compute_tax=True)

        account_tax_obj = self.env["account.tax"]
        avatax_config = self.company_id.get_avatax_config_company()

        # Make sure Avatax is configured
        if not avatax_config:
            raise UserError(
                _(
                    "Your Avatax Countries settings are not configured. "
                    "You need a country code in the Countries section.  \n"
                    "If you have a multi-company installation, "
                    "you must add settings for each company.  \n\n"
                    "You can update settings in Avatax->Avatax API."
                )
            )

        tax_amount = o_tax_amt = 0.0

        # ship from Address / Origin Address either warehouse or company if none
        ship_from_address_id = (
            self.warehouse_id.partner_id or self.company_id.partner_id
        )

        compute_taxes = (
            self.env.context.get("avatax_recomputation")
            or avatax_config.enable_immediate_calculation
        )
        if compute_taxes:
            ava_tax = account_tax_obj.search(
                [
                    ("is_avatax", "=", True),
                    ("type_tax_use", "in", ["sale", "all"]),
                    ("company_id", "=", self.company_id.id),
                ]
            )

            shipping_add_id = self.tax_add_id

            lines = self._avatax_prepare_lines()

            order_date = self.date_order.date()
            if lines:
                doc_type = "SalesOrder"
                if avatax_config.on_line:
                    # Line level tax calculation
                    # tax based on individual order line
                    tax_id = []
                    for line in lines:
                        tax_id = (
                            line["tax_id"] and [tax.id for tax in line["tax_id"]] or []
                        )
                        if ava_tax and ava_tax[0].id not in tax_id:
                            tax_id.append(ava_tax[0].id)
                        tax_result = account_tax_obj._get_compute_tax(  # SOAP
                            avatax_config,
                            order_date,
                            self.name,
                            doc_type,
                            self.partner_id,
                            ship_from_address_id,
                            shipping_add_id,
                            [line],
                            self.user_id,
                            self.exemption_code or None,
                            self.exemption_code_id.code or None,
                            currency_id=self.currency_id,
                        )
                        ol_tax_amt = tax_result.TotalTax
                        o_tax_amt += (
                            ol_tax_amt  # tax amount based on total order line total
                        )
                        line["id"].write(
                            {"tax_amt": ol_tax_amt, "tax_id": [(6, 0, tax_id)]}
                        )

                    tax_amount = o_tax_amt

                elif avatax_config.on_order:
                    tax_result = account_tax_obj._get_compute_tax(  # SOAP
                        avatax_config,
                        order_date,
                        self.name,
                        doc_type,
                        self.partner_id,
                        ship_from_address_id,
                        shipping_add_id,
                        lines,
                        self.user_id,
                        self.exemption_code or None,
                        self.exemption_code_id.code or None,
                        currency_id=self.currency_id,
                    )
                    tax_amount = tax_result.TotalTax

                    for o_line in self.order_line:
                        o_line.write({"tax_amt": 0.0})
                else:
                    raise UserError(
                        _("Please select system calls in Avatax API Configuration")
                    )
            else:
                self.order_line.write({"tax_amt": 0.0})

        else:
            self.order_line.write({"tax_amt": 0.0})

        self.write({"tax_amount": tax_amount, "order_line": []})
        return True

    def _avatax_compute_tax(self):
        """ Contact REST API and recompute taxes for a Sale Order """
        self and self.ensure_one()
        doc_type = self._get_avatax_doc_type()
        Tax = self.env["account.tax"]
        avatax_config = self.company_id.get_avatax_config_company()
        taxable_lines = self._avatax_prepare_lines(doc_type)
        tax_result = avatax_config.create_transaction(
            self.date_order,
            self.name,
            doc_type,
            self.partner_id,
            self.warehouse_id.partner_id or self.company_id.partner_id,
            self.partner_shipping_id or self.partner_id,
            taxable_lines,
            self.user_id,
            self.exemption_code or None,
            self.exemption_code_id.code or None,
            currency_id=self.currency_id,
        )
        tax_result_lines = {int(x["lineNumber"]): x for x in tax_result["lines"]}
        for line in self.order_line:
            tax_result_line = tax_result_lines.get(line.id)
            if tax_result_line:
                rate = tax_result_line.get("rate", doc_type)
                tax = Tax.get_avalara_tax(rate, doc_type)
                if tax and not (tax == line.tax_id.filtered("is_avatax")):
                    non_avataxes = line.tax_id.filtered(lambda x: not x.is_avatax)
                    line.tax_id = non_avataxes | tax
                line.tax_amt = tax_result_line["tax"]
        self.tax_amount = tax_result.get("totalTax")
        # Force tax totals recomputation, to ensure teh Avatax amount is applied
        self._amount_all()
        return True

    @api.multi
    def _avalara_compute_taxes(self):
        self and self.ensure_one()
        has_avatax_tax = self.mapped("order_line.tax_id.is_avatax")
        avatax_config = self.company_id.get_avatax_config_company()
        if not (has_avatax_tax and avatax_config):
            self.write({"tax_amount": 0})
        elif "rest" in avatax_config.service_url:
            self._avatax_compute_tax()
        else:
            self.with_context(avatax_recomputation=True).compute_tax()
        return True

    @api.multi
    def avalara_compute_taxes(self):
        """
        Used for manual recalculation of Avalara taxes
        from button on Sales Order form
        """
        self and self.ensure_one()
        if self.state in ["done", "cancel"]:
            raise UserError(
                _("Cannot recompute taxes on Locked and Cancelled Sales Orders.")
            )
        return self._avalara_compute_taxes()

    @api.multi
    def action_confirm(self):
        self and self.ensure_one()
        avatax_config = self.company_id.get_avatax_config_company()
        if avatax_config and avatax_config.force_address_validation:
            for addr in [self.partner_id, self.partner_shipping_id]:
                if not addr.date_validation:
                    # The Confirm action will be interrupted
                    # if the address is not validated
                    return addr.button_avatax_validate_address()
        res = super(SaleOrder, self).action_confirm()
        if avatax_config:
            self._avalara_compute_taxes()
        return res


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    tax_amt = fields.Float("Avalara Tax", help="tax calculate by avalara")

    @api.onchange("product_uom_qty", "discount", "price_unit", "tax_id")
    def onchange_reset_avatax_amount(self):
        """
        When changing quantities or prices, reset the Avatax computed amount.
        The Odoo computed tax amount will then be shown, as a reference.
        The Avatax amount will be recomputed upon document validation.
        """
        for line in self:
            line.tax_amt = 0
            line.order_id.tax_amount = 0

    def _avatax_prepare_line(self, sign=1, doc_type=None):
        """
        Prepare a line to use for Avatax computation.
        Returns a dict
        """
        line = self
        res = {}
        if line.tax_id.filtered("is_avatax"):
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
                sign
                * line.price_unit
                * line.product_uom_qty
                * (1 - line.discount / 100.0)
            )
            # Calculate discount amount
            discount_amount = 0.0
            is_discounted = False
            if line.discount:
                discount_amount = (
                    sign
                    * line.price_unit
                    * line.product_uom_qty
                    * line.discount
                    / 100.0
                )
                is_discounted = True
            res = {
                "qty": line.product_uom_qty,
                "itemcode": line.product_id and item_code or None,
                "description": line.name,
                "discounted": is_discounted,
                "discount": discount_amount,
                "amount": amount,
                "tax_code": tax_code,
                "id": line,
                "tax_id": line.tax_id,
            }
        return res

    def _get_tax_price_unit(self):
        """
        Returns the Base Amount to use for Tax.
        """
        self.ensure_one()
        return self.price_unit * (1 - (self.discount or 0.0) / 100.0)

    @api.depends("product_uom_qty", "discount", "price_unit", "tax_id", "tax_amt")
    def _compute_amount(self):
        """
        If we have a Avatax computed amount, use it instead of the Odoo computed one
        """
        super()._compute_amount()
        for line in self:
            # Use line price
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            tax_ids = line.tax_id.with_context(avatax_line=line)
            taxes = tax_ids.compute_all(
                price,
                line.order_id.currency_id,
                line.product_uom_qty,
                product=line.product_id,
                partner=line.order_id.partner_shipping_id,
            )
            vals = {
                "price_tax": taxes["total_included"] - taxes["total_excluded"],
                "price_total": taxes["total_included"],
                "price_subtotal": taxes["total_excluded"],
            }
            line.update(vals)
