import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from .avalara_api import AvaTaxService, BaseAddress
from .avatax_rest_api import AvaTaxRESTService


_logger = logging.getLogger(__name__)


class AccountTax(models.Model):
    """Inherit to implement the tax using avatax API"""

    _inherit = "account.tax"

    is_avatax = fields.Boolean("Is Avatax")

    @api.model
    def _get_avalara_tax_domain(self, tax_rate, doc_type):
        return [
            ("amount", "=", tax_rate),
            ("is_avatax", "=", True),
        ]

    @api.model
    def _get_avalara_tax_name(self, tax_rate, doc_type=None):
        return _("AVT-Sales {}%").format(str(tax_rate))

    @api.model
    def get_avalara_tax(self, tax_rate, doc_type):
        tax = self.with_context(active_test=False).search(
            self._get_avalara_tax_domain(tax_rate, doc_type), limit=1
        )
        if tax and not tax.active:
            tax.active = True
        if not tax:
            tax_template = self.search(
                self._get_avalara_tax_domain(0, doc_type), limit=1
            )
            tax = tax_template.sudo().copy(default={"amount": tax_rate})
            # If you get a unique constraint error here,
            # check the data for your existing Avatax taxes.
            tax.name = self._get_avalara_tax_name(tax_rate, doc_type)
        return tax

    def _avatax_amount_compute_all(self):
        avatax_amount = None
        avatax_line = self.env.context.get("avatax_line")
        if avatax_line:
            avatax_result = self.env.context.get("avatax_result")
            if avatax_result:  # force Avatax returned amounts
                avatax_result_lines = {
                    int(x["lineNumber"]): x for x in avatax_result["lines"]
                }
                avatax_result_line = avatax_result_lines.get(avatax_line.id, {})
                avatax_amount = abs(avatax_result_line.get("tax", 0))
            elif avatax_line.tax_amt:
                # Use the last Avatax returned amount, or
                # Recompute taxes using the configured
                # taxable price (may differ from price)
                avatax_amount = avatax_line.tax_amt
        return avatax_amount

    def compute_all(
        self, price_unit, currency=None, quantity=1.0, product=None, partner=None
    ):
        """
        Adopted as the central point to inject custom tax computations.

        May be called for two different purposes:

        a) from InvoiceLine._compute_price(), to set line amount before and after taxes.
        b) from Invoice.get_taxes_values(), to set tax amount and base summary lines.

        For this the context may contain:

        - avatax_line: the line record being computed.
          Its presence triggers the Avatax extension logic.
        - avatax_result: the response from the Avatax service.
          If available, will force the tax amounts returned.
          In not, uses odoo computation to estimate the taxes.
          The base amounts are kept, the tax amount is overriden.

        RETURN: {
            'total_excluded': 0.0,    # Total without taxes
            'total_included': 0.0,    # Total with taxes
            'base': 0.0,              # Taxable amount
            'taxes': [{               # One dict for each tax in self and their children
                'id': int,
                'name': str,
                'amount': float,
                'base': float,
                'sequence': int,
                'account_id': int,
                'refund_account_id': int,
                'analytic': boolean,
            }]
        """
        res = super().compute_all(price_unit, currency, quantity, product, partner)
        avatax_line = self.env.context.get("avatax_line")
        if avatax_line:
            avatax_amount = self._avatax_amount_compute_all()
            if not avatax_amount:
                avatax_amount = res["total_included"] - res["total_excluded"]
                new_price_unit = avatax_line._get_tax_price_unit()
                if price_unit != new_price_unit:
                    new_res = super().compute_all(
                        new_price_unit, currency, quantity, product, partner
                    )
                    avatax_amount = (
                        new_res["total_included"] - new_res["total_excluded"]
                    )
            for tax_item in res["taxes"]:
                if tax_item["amount"] != 0:
                    tax_item["amount"] = avatax_amount
            res["total_included"] = res["total_excluded"] + avatax_amount
        return res

    @api.model
    def _get_compute_tax(
        self,
        avatax_config,
        doc_date,
        doc_code,
        doc_type,
        partner,
        ship_from_address,
        shipping_address,
        lines,
        user=None,
        exemption_number=None,
        exemption_code_name=None,
        commit=False,
        invoice_date=False,
        reference_code=False,
        location_code=False,
        is_override=False,
        currency_id=False,
    ):

        currency_code = self.env.user.company_id.currency_id.name
        if currency_id:
            currency_code = currency_id.name

        if not partner.customer_code:
            if not avatax_config.auto_generate_customer_code:
                raise UserError(
                    _(
                        "Customer Code for customer %s not defined.\n\n  "
                        "You can edit the Customer Code in customer profile. "
                        'You can fix by clicking "Generate Customer Code" button '
                        'in the customer contact information"' % (partner.name)
                    )
                )
            else:
                partner.generate_cust_code()

        if not shipping_address:
            raise UserError(
                _("There is no source shipping address defined " "for partner %s.")
                % partner.name
            )

        if not ship_from_address:
            raise UserError(_("There is no company address defined."))

        # this condition is required, in case user select
        # force address validation on AvaTax API Configuration
        if not avatax_config.address_validation:
            if avatax_config.force_address_validation:
                if not shipping_address.date_validation:
                    raise UserError(
                        _(
                            "Please validate the shipping address for the partner %s."
                            % (partner.name)
                        )
                    )

            # if not avatax_config.address_validation:
            if not ship_from_address.date_validation:
                raise UserError(_("Please validate the company address."))

        if avatax_config.disable_tax_calculation:
            _logger.info(
                "Avatax tax calculation is disabled. Skipping %s %s.",
                doc_code,
                doc_type,
            )
            return False

        if "rest" in avatax_config.service_url:
            avatax_restpoint = AvaTaxRESTService(
                avatax_config.account_number,
                avatax_config.license_key,
                avatax_config.service_url,
                avatax_config.request_timeout,
                avatax_config.logging,
            )
            tax_result = avatax_restpoint.get_tax(
                avatax_config.company_code,
                doc_date,
                doc_type,
                partner.customer_code,
                doc_code,
                ship_from_address,
                shipping_address,
                lines,
                exemption_number,
                exemption_code_name,
                user and user.name or None,
                commit,
                invoice_date,
                reference_code,
                location_code,
                currency_code,
                partner.vat_id or None,
                is_override,
            )
            return tax_result
        else:
            # For check credential
            avalara_obj = AvaTaxService(
                avatax_config.account_number,
                avatax_config.license_key,
                avatax_config.service_url,
                avatax_config.request_timeout,
                avatax_config.logging,
            )
            avalara_obj.create_tax_service()
            addSvc = avalara_obj.create_address_service().addressSvc
            origin = BaseAddress(
                addSvc,
                ship_from_address.street or None,
                ship_from_address.street2 or None,
                ship_from_address.city,
                ship_from_address.zip,
                ship_from_address.state_id and ship_from_address.state_id.code or None,
                ship_from_address.country_id
                and ship_from_address.country_id.code
                or None,
                0,
            ).data
            destination = BaseAddress(
                addSvc,
                shipping_address.street or None,
                shipping_address.street2 or None,
                shipping_address.city,
                shipping_address.zip,
                shipping_address.state_id and shipping_address.state_id.code or None,
                shipping_address.country_id
                and shipping_address.country_id.code
                or None,
                1,
            ).data

            # using get_tax method to calculate tax based on address
            result = avalara_obj.get_tax(
                avatax_config.company_code,
                doc_date,
                doc_type,
                partner.customer_code,
                doc_code,
                origin,
                destination,
                lines,
                exemption_number,
                exemption_code_name,
                user and user.name or None,
                commit,
                invoice_date,
                reference_code,
                location_code,
                currency_code,
                partner.vat_id or None,
                is_override,
            )
        return result

    @api.model
    def cancel_tax(self, avatax_config, doc_code, doc_type, cancel_code):
        if avatax_config.disable_tax_calculation:
            _logger.info(
                "Avatax tax calculation is disabled. Skipping %s %s.",
                doc_code,
                doc_type,
            )
            return False
        if "rest" in avatax_config.service_url:
            avatax_restpoint = AvaTaxRESTService(
                avatax_config.account_number,
                avatax_config.license_key,
                avatax_config.service_url,
                avatax_config.request_timeout,
                avatax_config.logging,
            )
            result = avatax_restpoint.cancel_tax(
                avatax_config.company_code, doc_code, doc_type, cancel_code
            )
        else:
            avalara_obj = AvaTaxService(
                avatax_config.account_number,
                avatax_config.license_key,
                avatax_config.service_url,
                avatax_config.request_timeout,
                avatax_config.logging,
            )
            avalara_obj.create_tax_service()
            # Why the silent failure? Let explicitly raise the error.
            # try:
            result = avalara_obj.get_tax_history(
                avatax_config.company_code, doc_code, doc_type
            )
            # except:
            #    return True
            result = avalara_obj.cancel_tax(
                avatax_config.company_code, doc_code, doc_type, cancel_code
            )
        return result
