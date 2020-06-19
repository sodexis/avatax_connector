{
    "name": "Use Tax on Customer Invoices with Avalara Avatax Connector",
    "version": "12.0.1.0.0",
    "author": "Open Source Integrators",
    "summary": "Use tax Calculation on Invoices and Sale Orders",
    "license": "AGPL-3",
    "description": """
    Use tax Calculation on Invoices and Sale Orders
    """,
    "category": "Generic Modules/Accounting",
    "depends": [
        'avatax_connector',
        # Found in https://github.com/OCA/account-fiscal-rule:
        'account_tax_expensed',
        'account_tax_expensed_sale',
        'account_tax_python_percentage',
    ],
    "data": [
        "data/avatax_data.xml",
    ],
    "demo": [
        "demo/fiscal_position_demo.xml",
    ],
    'installable': True,
    'auto_install': True,
}
