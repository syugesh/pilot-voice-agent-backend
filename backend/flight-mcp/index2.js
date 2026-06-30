import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { tavily } from "@tavily/core";

const client = tavily({ apiKey: process.env.TAVILY_API_KEY });

const server = new McpServer({
  name: "customer-care-mcp",
  version: "1.0.0",
});

server.tool(
  process.env.PILOT_MCP_TOOL || "search_flights_web",
  {
    serviceType: z.enum(["flights", "hotels", "trains", "cabs"]),
    origin: z.string().optional(),
    destination: z.string().optional(),
    query: z.string().optional(),
  },
  async ({ serviceType, origin, destination, query }) => {
    const today = new Date().toISOString().split("T")[0];

    let searchQuery;
    switch (serviceType) {
      case "flights":
        searchQuery = `list at least 5 different flight ticket prices from ${origin} to ${destination} today ${today} with price details on all platforms.`;
        break;
      case "hotels":
        searchQuery = `hotel booking customer care ${origin || "city"} ${today} MakeMyTrip Goibibo Cleartrip`;
        break;
      case "trains":
        searchQuery = `train booking customer care ${origin} to ${destination} ${today} IRCTC MakeMyTrip Goibibo`;
        break;
      case "cabs":
        searchQuery = `cab taxi booking customer care ${origin} to ${destination} ${today} Ola Uber Rapido`;
        break;
      default:
        searchQuery = query || "general customer care services";
    }

    try {
      const response = await client.search(searchQuery, {
        searchDepth: "basic",
        maxResults: 5,
        includeAnswer: true,
      });

      return {
        content: [{
          type: "text",
          text: JSON.stringify({
            serviceType,
            route: origin && destination ? `${origin} → ${destination}` : origin || destination || "",
            date: today,
            summary: response.answer,
            results: response.results.map(r => ({
              title: r.title,
              url: r.url,
              snippet: r.content,
            })),
            source: "web"
          })
        }]
      };

    } catch (err) {
      // fallback to static data for each service type
      console.error("Tavily search failed, using fallback:", err?.message);
      let fallbackResults;
      if (serviceType === "flights") {
        fallbackResults = [
          { airline: "IndiGo",    flight: "6E-204",  departure: "06:00", price: "₹4200", customerCare: "0124-6173838" },
          { airline: "Air India", flight: "AI-865",  departure: "07:30", price: "₹5800", customerCare: "1860-233-1407" },
          { airline: "SpiceJet",  flight: "SG-157",  departure: "09:15", price: "₹3900", customerCare: "1800-102-2333" },
          { airline: "Vistara",   flight: "UK-935",  departure: "11:00", price: "₹6500", customerCare: "1860-233-1407" },
          { airline: "Akasa Air", flight: "QP-1374", departure: "14:30", price: "₹3600", customerCare: "1800-102-3333" },
        ];
      } else if (serviceType === "hotels") {
        fallbackResults = [
          { hotel: "Hotel Taj", location: origin, phone: "1800-266-7646" },
          { hotel: "Hotel Oberoi", location: origin, phone: "1800-102-2333" },
        ];
      } else if (serviceType === "trains") {
        fallbackResults = [
          { operator: "IRCTC", phone: "139" },
          { operator: "RailYatri", phone: "8010500300" },
        ];
      } else if (serviceType === "cabs") {
        fallbackResults = [
          { operator: "Ola", phone: "0120-3355335" },
          { operator: "Uber", phone: "080-4685-2190" },
        ];
      } else {
        fallbackResults = [
          { note: "Customer care service type not recognized." }
        ];
      }

      return {
        content: [{
          type: "text",
          text: JSON.stringify({
            serviceType,
            route: origin && destination ? `${origin} → ${destination}` : origin || destination || "",
            date: today,
            results: fallbackResults,
            source: "fallback — Tavily unavailable"
          })
        }]
      };
    }
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
console.error("Customer Care MCP server started");
