# Installation Guide: Airtel Cable/DOCSIS Service with SFP Optical Module

**Document ID:** INSTALL-0014
**Category:** Installation Guide
**Applies To:** Airtel Cable/DOCSIS, SFP Optical Module
**Audience:** Field Technicians / Self-Install Customers
**Last Reviewed:** 2025-08-15

## Summary
Provides the complete step-by-step installation and activation procedure for new Airtel Cable/DOCSIS service using a SFP Optical Module, covering both professional installation and customer self-install kits.

## Pre-Installation Checklist
- Confirm the service order in the OSS shows the correct address and Cable/DOCSIS availability confirmation.
- Verify the customer has a working electrical outlet within 6 feet of the planned equipment location.
- For fiber installs, confirm an ONT mounting location has been identified, ideally near the point of entry.
- For DSL/cable installs, confirm the demarcation point (NID or tap) is accessible and in good condition.

## Installation Steps
1. **Physical Connection:** Connect the coax or telephone line from the wall jack to the WAN port of the SFP Optical Module using the provided cable.
2. **Power On:** Plug in the SFP Optical Module power adapter and wait 3-5 minutes for the device to fully boot and attempt line synchronization.
3. **Verify Sync:** Confirm the Cable/DOCSIS sync/online LED transitions to solid green, indicating a successful physical layer connection.
4. **Provisioning Activation:** From the OSS/provisioning system, activate the service order which triggers the SFP Optical Module to authenticate via PPPoE or DHCP and receive its WAN IP configuration.
5. **WiFi Setup:** Access the SFP Optical Module admin portal at 192.168.1.1, configure the WiFi SSID and password per the customer's preference, and ensure WPA2/WPA3 security is enabled.
6. **Speed Test Validation:** Connect a laptop via Ethernet directly to the SFP Optical Module and run a speed test against the ISP's speed test server, confirming results are within 10% of the provisioned plan speed.
7. **VoIP Activation (if applicable):** If the customer has a voice plan, connect the phone to the RJ-11 port, confirm dial tone, and place a test call to verify outbound/inbound functionality including E911 registration.
8. **Customer Walkthrough:** Show the customer the device LEDs, admin portal login, and how to power cycle the device if issues arise. Provide the customer with the account PIN for future authentication.

## Post-Installation Verification
- Confirm the account status in the CRM shows "Active - Service Confirmed."
- Log the SFP Optical Module serial number, MAC address, and installed firmware version in the account record.
- Schedule an automated 24-hour follow-up check to confirm no immediate stability issues.

## Common Installation Issues
- If the Cable/DOCSIS sync LED does not turn solid green within 10 minutes, check for physical layer issues (damaged cable, incorrect port, low optical power) before escalating.
- If provisioning activation fails, confirm the service order was submitted correctly and that the device's MAC address matches what was registered during the order process.

## Related Documents
- Product Manual: MAN-0015
- Troubleshooting Guide: TS-0015
- SOP: SOP-0015

## Tags
installation, cable/docsis, sfp optical module, airtel, provisioning
