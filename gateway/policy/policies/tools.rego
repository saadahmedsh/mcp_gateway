package mcp_gateway

default decision := {
  "outcome": "deny",
  "matched_rule": "default_deny",
  "reason": "No policy rule matched"
}

decision := {
  "outcome": "deny",
  "matched_rule": "invalid_principal",
  "reason": "A verified tenant and supported role are required"
} if {
  input.principal
  not caller_authorized
}

decision := {
  "outcome": "allow",
  "matched_rule": "read_only_tool",
  "reason": "Read-only tools are allowed"
} if {
  input.risk_class == "read_only"
  caller_authorized
  not destructive
}

decision := {
  "outcome": "requires_approval",
  "matched_rule": "destructive_operation",
  "reason": "Destructive operations require explicit approval and a reason"
} if {
  destructive
  caller_elevated
}

decision := {
  "outcome": "requires_approval",
  "matched_rule": "mutating_tool",
  "reason": "Mutating tools require explicit approval"
} if {
  input.risk_class == "mutating"
  caller_elevated
  not destructive
}

decision := {
  "outcome": "requires_approval",
  "matched_rule": "destructive_risk_class",
  "reason": "Destructive tools require explicit approval and a reason"
} if {
  input.risk_class == "destructive"
  caller_elevated
  not destructive
}

caller_authorized if {
  not input.principal
}

caller_authorized if {
  input.principal.tenant_id != ""
  some role in input.principal.roles
  role in {"user", "operator", "admin"}
}

caller_elevated if {
  not input.principal
}

caller_elevated if {
  input.principal.tenant_id != ""
  some role in input.principal.roles
  role in {"operator", "admin"}
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
