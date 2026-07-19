"""Claude tool schemas for Tempo wallet and MPP operations."""

from calbot.tempo.payments import MAX_MONEY_TEXT_CHARS


TEMPO_TOOLS = [
    {
        "name": "tempo_wallet_balance",
        "description": "Check the Tempo wallet balance and address.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "tempo_discover_services",
        "description": (
            "Search for available Tempo services by keyword "
            "(e.g. 'parallel', 'image', 'search', 'audio', 'browser')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Search keyword",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tempo_service_details",
        "description": (
            "Get full details (URL, endpoints, pricing) for a specific Tempo service by its ID. "
            "Always call this before tempo_call_service; it authorizes only the exact discovered "
            "endpoint URLs and methods for payment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "maxLength": 64},
            },
            "required": ["service_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tempo_call_service",
        "description": (
            "Call a Tempo service endpoint with automatic stablecoin payment via MPP. "
            "Always use tempo_service_details first to get the exact URL, method, and body schema. "
            "Never guess endpoint paths. Every new service call requires the user's exact, "
            "actor-bound confirmation before it runs, including zero-cost calls. Only status "
            "polls created from a previously approved task may run without another confirmation. "
            "Never retry a submitted paid call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "maxLength": 2048,
                    "description": "Full service URL including path, from tempo_service_details",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "description": "HTTP method, e.g. GET or POST",
                    "default": "POST",
                },
                "body": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "JSON body as a string (for POST requests)",
                },
                "max_spend": {
                    "type": "string",
                    "maxLength": MAX_MONEY_TEXT_CHARS,
                    "description": (
                        "Per-call spend cap in USDC, required for dynamic pricing. It may be lower "
                        "than but never higher than the bot's configured ceiling."
                    ),
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tempo_task_status",
        "description": (
            "Check a previously submitted Parallel task using its exact run_id. "
            "Use only a run_id returned by the executor or written by the user. "
            "This fixed status endpoint is always invoked with max spend zero."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9_-]+$",
                    "description": "Exact validated Parallel run ID",
                }
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
]
