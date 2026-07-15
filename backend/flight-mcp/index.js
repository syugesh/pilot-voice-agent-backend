import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { tavily } from "@tavily/core";

const client = tavily({ apiKey: process.env.TAVILY_API_KEY });

const server = new McpServer({
  name: "flight-mcp",
  version: "1.0.0",
});

server.tool(
  process.env.PILOT_MCP_TOOL,
  {
    origin: z.string(),
    destination: z.string(),
    query: z.string().optional(),
  },
  async ({ origin, destination }) => {
    const today = new Date().toISOString().split("T")[0];
    const searchQuery = `flight prices ${origin} to ${destination} today ${today} MakeMyTrip Goibibo Cleartrip`;

    try {
      const response = await client.search(searchQuery, {
        searchDepth: "advanced",
        maxResults: 5,
        includeAnswer: true,
      });

      return {
        content: [{
          type: "text",
          text: JSON.stringify({
            route: `${origin} → ${destination}`,
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
      // Never invent flight data — an honest empty result is correct here,
      // a plausible-looking fabricated one is not.
      console.error("Tavily search failed:", err.message);
      return {
        content: [{
          type: "text",
          text: JSON.stringify({
            route: `${origin} → ${destination}`,
            date: today,
            results: [],
            source: "error — Tavily unavailable",
            error: err.message,
          })
        }]
      };
    }
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
console.error("Flight MCP server started");