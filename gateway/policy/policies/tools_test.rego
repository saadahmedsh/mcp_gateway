package mcp_gateway

test_read_only_select if {
  decision with input as {
    "tool_name": "db_query",
    "risk_class": "read_only",
    "arguments": {"query": "SELECT * FROM orders"}
  } == {
    "outcome": "allow",
    "matched_rule": "read_only_tool",
    "reason": "Read-only tools are allowed"
  }
}

test_mutating_requires_approval if {
  decision with input as {
    "tool_name": "git_workspace",
    "risk_class": "mutating",
    "arguments": {}
  } == {
    "outcome": "requires_approval",
    "matched_rule": "mutating_tool",
    "reason": "Mutating tools require explicit approval"
  }
}

test_destructive_risk_requires_approval if {
  decision with input as {
    "tool_name": "shell_exec",
    "risk_class": "destructive",
    "arguments": {"command": "printf safe"}
  } == {
    "outcome": "requires_approval",
    "matched_rule": "destructive_risk_class",
    "reason": "Destructive tools require explicit approval and a reason"
  }
}

test_delete_without_where_requires_approval if {
  decision with input as {
    "tool_name": "db_query",
    "risk_class": "read_only",
    "arguments": {"query": "DELETE FROM orders"}
  } == {
    "outcome": "requires_approval",
    "matched_rule": "destructive_operation",
    "reason": "Destructive operations require explicit approval and a reason"
  }
}

test_safe_delete_with_where_is_allowed_by_declared_risk if {
  decision with input as {
    "tool_name": "db_query",
    "risk_class": "read_only",
    "arguments": {"query": "DELETE FROM orders WHERE order_id = 'ORD-1001'"}
  } == {
    "outcome": "allow",
    "matched_rule": "read_only_tool",
    "reason": "Read-only tools are allowed"
  }
}
