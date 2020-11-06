{
    "name": "Use Tax on Customer Invoices with Avalara Avatax Connector",
    "version": "12.0.1.0.1",
    "author": "Open Source Integrators",
    "summary": "Use tax Calculation on Invoices and Sale Orders",
    "license": "AGPL-3",
    "description": """
    Use tax Calculation on Invoices and Sale Orders
    """,
    "category": "Generic Modules/Accounting",
    "depends": [
        'sale_margin',
        'avatax_connector',
        # Found in https://github.com/OCA/margin-analysis:
        'account_invoice_margin',
    ],
    "data": [
        "data/avatax_data.xml",
        "views/account_tax.xml",
        "views/account_invoice.xml",
        "views/sale_order.xml",
    ],
    "demo": [
        "demo/fiscal_position_demo.xml",
    ],
    'installable': True,
    'auto_install': True,
}
