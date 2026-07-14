# Product Manual: Sagemcom F@st 5566 Gateway

**Document ID:** MAN-0033
**Category:** Product Manual
**Device Type:** Sagemcom F@st 5566 Gateway
**Compatible Service:** Comcast Fixed Wireless
**Firmware Baseline:** v5.3.19
**Last Reviewed:** 2025-10-22

## Summary
Technical reference manual for support agents covering the Sagemcom F@st 5566 Gateway, including hardware specifications, LED indicator meanings, default configuration, and supported protocols for Comcast Fixed Wireless deployments.

## Hardware Overview
The Sagemcom F@st 5566 Gateway supports Fixed Wireless connectivity and includes an integrated 2.5G multi-gig WAN/LAN port, dual-band 802.11ax WiFi radios, and an integrated VoIP ATA with two RJ-11 ports.

## LED Indicator Reference
| LED | State | Meaning |
|---|---|---|
| Power | Solid Green | Device powered and booted |
| Power | Blinking Red | Firmware corruption / boot failure |
| WAN/Internet | Solid Green | WAN link authenticated, internet reachable |
| WAN/Internet | Blinking Amber | Attempting PPPoE/DHCP authentication |
| WAN/Internet | Solid Red | Loss of signal (LOS) or WAN down |
| WiFi | Solid Blue | Wireless radios active |
| Fixed Wireless Sync | Solid Green | Line synced within normal parameters |
| Fixed Wireless Sync | Off | No sync - check physical cabling |

## Default Configuration
- Default gateway IP: 192.168.1.1 (or 192.168.0.1 on legacy units)
- Default admin credentials: printed on device label; must be changed on first login per security policy
- DHCP pool: 192.168.1.100 - 192.168.1.199 (100 addresses)
- WAN protocol support: PPPoE, DHCP (IPoE), Static IP, IPv6 (DHCPv6-PD, SLAAC)

## Supported Protocols and Features
- IPv4 and IPv6 dual-stack
- DNS relay and custom DNS server configuration
- Port forwarding, DMZ, and UPnP
- VPN passthrough (IPSec, PPTP, L2TP) and select models support built-in WireGuard/OpenVPN client
- QoS traffic prioritization for VoIP and gaming traffic
- WPA2-Personal and WPA3-Personal WiFi security
- TR-069 (CWMP) remote management for ISP provisioning

## Common Configuration Tasks
1. **Changing WiFi SSID/Password:** Navigate to Wireless Settings > Basic, update SSID and Security Key, click Apply, and allow 60 seconds for radios to restart.
2. **Enabling Bridge Mode:** Navigate to Advanced > WAN Settings, select Bridge Mode, and confirm; note this disables NAT/DHCP/firewall on the device.
3. **Firmware Update:** Navigate to Administration > Firmware Update; the device checks the Comcast provisioning server automatically and can be triggered manually.
4. **Factory Reset:** Hold the recessed reset button for 10-15 seconds until all LEDs flash simultaneously.

## Related Documents
- Installation Guide: INSTALL-0034
- Firmware Release Notes: FW-0034
- Troubleshooting Guide: TS-0034

## Tags
product-manual, fixed wireless, sagemcom f@st 5566 gateway, hardware-reference, comcast
