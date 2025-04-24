# solar-automation

Idea is to disable solar inverter export to grid when electricity price is below certain threshold, which would start to generate financial loss considering operator margin and possibility of even negative prices.
Current solution is expected to work with specific Solax inverter model X3-Hybrid-G4. Electricity prices are monitored using nordpool unofficial APIs and tracking EE area.

Configuration:
SOLAX_REG_NO = os.environ.get('SOLAX_REG_NO')
SOLAX_SERIAL = os.environ.get('SOLAX_SERIAL')
SOLAX_TOKEN_ID = os.environ.get('SOLAX_TOKEN_ID')

These can be figured out when accessing your solar inverter in https://global.solaxcloud.com/ web interface. Open developer tools in Chromium based browser, check network tab and inspect requests which would need to access the inverter.
