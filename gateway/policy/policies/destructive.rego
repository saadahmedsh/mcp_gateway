package mcp_gateway.patterns

drop_statement if {
  contains(upper(input.arguments.query), "DROP")
}

truncate_statement if {
  contains(upper(input.arguments.query), "TRUNCATE")
}
