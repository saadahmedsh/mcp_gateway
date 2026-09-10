package mcp_gateway

default decision := {
  "outcome": "deny",
  "matched_rule": "default_deny",
  "reason": "No policy rule matched"
}

decision := {
  "outcome": "allow",
  "matched_rule": "read_only_tool",
  "reason": "Read-only tools are allowed"
} if {
  input.risk_class == "read_only"
  not destructive
}

decision := {
  "outcome": "requires_approval",
  "matched_rule": "destructive_operation",
  "reason": "Destructive operations require explicit approval and a reason"
} if {
  destructive
}

decision := {
  "outcome": "requires_approval",
  "matched_rule": "mutating_tool",
  "reason": "Mutating tools require explicit approval"
} if {
  input.risk_class == "mutating"
  not destructive
}

decision := {
  "outcome": "requires_approval",
  "matched_rule": "destructive_risk_class",
  "reason": "Destructive tools require explicit approval and a reason"
} if {
  input.risk_class == "destructive"
  not destructive
}

destructive if {
  input.tool_name == "db_query"
  contains(upper(input.arguments.query), "DROP")
}

destructive if {
  input.tool_name == "db_query"
  contains(upper(input.arguments.query), "TRUNCATE")
}

destructive if {
  input.tool_name == "db_query"
  query := upper(input.arguments.query)
  startswith(query, "DELETE")
  not contains(query, " WHERE ")
}

destructive if {
  input.tool_name == "db_query"
  query := upper(input.arguments.query)
  startswith(query, "UPDATE")
  not contains(query, " WHERE ")
}

destructive if {
  input.tool_name == "shell_exec"
  contains(input.arguments.command, "..")
}
