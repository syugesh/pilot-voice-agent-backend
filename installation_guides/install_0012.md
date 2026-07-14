# Installation Guide: Verizon DSL Service with VoIP ATA

**Document ID:** INSTALL-0012
**Category:** Installation Guide
**Applies To:** Verizon DSL, VoIP ATA
**Audience:** Field Technicians / Self-Install Customers
**Last Reviewed:** 2025-08-13

## Summary
Provides the complete step-by-step installation and activation procedure for new Verizon DSL service using a VoIP ATA, covering both professional installation and customer self-install kits.

## Pre-Installation Checklist
- Confirm the service order in the OSS shows the correct address and DSL availability confirmation.
- Verify the customer has a working electrical outlet within 6 feet of the planned equipment location.
- For fiber installs, confirm an ONT mounting location has been identified, ideally near the point of entry.
- For DSL/cable installs, confirm the demarcation point (NID or tap) is accessible and in good condition.

## Installation Steps
1. **Physical Connection:** Connect the coax or telephone line from the wall jack to the WAN port of the VoIP ATA using the provided cable.
2. **Power On:** Plug in the VoIP ATA power adapter and wait 3-5 minutes for the device to fully boot and attempt line synchronization.
3. **Verify Sync:** Confirm the DSL sync/online LED transitions to solid green, indicating a successful physical layer connection.
4. **Provisioning Activation:** From the OSS/provisioning system, activate the service order which triggers the VoIP ATA to authenticate via PPPoE or DHCP and receive its WAN IP configuration.
5. **WiFi Setup:** Access the VoIP ATA admin portal at 192.168.1.1, configure the WiFi SSID and password per the customer's preference, and ensure WPA2/WPA3 security is enabled.
6. **Speed Test Validation:** Connect a laptop via Ethernet directly to the VoIP ATA and run a speed test against the ISP's speed test server, confirming results are within 10% of the provisioned plan speed.
7. **VoIP Activation (if applicable):** If the customer has a voice plan, connect the phone to the RJ-11 port, confirm dial tone, and place a test call to verify outbound/inbound functionality including E911 registration.
8. **Customer Walkthrough:** Show the customer the device LEDs, admin portal login, and how to power cycle the device if issues arise. Provide the customer with the account PIN for future authentication.

## Post-Installation Verification
- Confirm the account status in the CRM shows "Active - Service Confirmed."
- Log the VoIP ATA serial number, MAC address, and installed firmware version in the account record.
- Schedule an automated 24-hour follow-up check to confirm no immediate stability issues.

## Common Installation Issues
- If the DSL sync LED does not turn solid green within 10 minutes, check for physical layer issues (damaged cable, incorrect port, low optical power) before escalating.
- If provisioning activation fails, confirm the service order was submitted correctly and that the device's MAC address matches what was registered during the order process.

## Related Documents
- Product Manual: MAN-0013
- Troubleshooting Guide: TS-0013
- SOP: SOP-0013

## Tags
installation, dsl, voip ata, verizon, provisioning
