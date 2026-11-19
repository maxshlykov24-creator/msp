"""Install the transactional order list projection, including previously imported orders."""


def install(connection, schema):
    postgres = connection.dialect.name == 'postgresql'
    quote = connection.dialect.identifier_preparer.quote_schema
    prefix = quote(schema or connection.dialect.default_schema_name) + '.' if postgres else ''
    entities = prefix + 'entities'
    entries = prefix + 'order_list_entries'
    view = prefix + 'order_list_source'
    run = connection.exec_driver_sql

    # Both the initial backfill and subsequent writes use the exact same projection.
    if postgres:
        # Readers continue under MVCC. Capture all writes either in the backfill or trigger.
        run("SET LOCAL lock_timeout = '5s'")
        run(f'LOCK TABLE {entities} IN SHARE ROW EXCLUSIVE MODE')
        lines = "jsonb_array_elements(COALESCE((data->'lines')::jsonb, '[]')) AS lines(line)"
        aggregate = "string_agg"
        actionable = "COALESCE((data->>'needs_mapping')::boolean, false)"
    else:
        lines = "json_each(data, '$.lines') AS lines"
        aggregate = "group_concat"
        actionable = "COALESCE(data->>'needs_mapping', 0)"
    line = 'line' if postgres else 'lines.value'
    columns = ('platform', 'scheme', 'stage', 'created_at', 'attention', 'search_text')
    all_columns = ', '.join(('id', *columns))
    fields = ('external_id', 'order_number', 'order_id', 'source_ref', 'parent')
    identifiers = " || ' ' || ".join(f"COALESCE(data->>'{field}', '')" for field in fields)
    products = " || ' ' || ".join(f"COALESCE({line}->>'{field}', '')" for field in ('barcode', 'sku', 'offer_id'))
    run(f"""CREATE VIEW {view} AS
        SELECT id, COALESCE(data->>'platform', '') AS platform, COALESCE(data->>'scheme', '') AS scheme,
          CASE WHEN data->>'platform' = 'ozon' AND data->>'raw_status' = 'awaiting_packaging' THEN 'new'
               WHEN data->>'platform' = 'ozon' AND data->>'raw_status' IN ('arbitration','client_arbitration') THEN 'shipping'
               ELSE COALESCE(data->>'status', 'unknown') END AS stage,
          COALESCE(data->>'created_at', '') AS created_at,
          ({actionable} OR COALESCE(data->>'accounting_state','') IN ('shortage','needs_review','waiting_stock')) AS attention,
          {identifiers} || ' ' || COALESCE((SELECT {aggregate}({products}, ' ') FROM {lines}), '') AS search_text
        FROM {entities} WHERE kind = 'market_order'""")
    updates = ', '.join(f'{c} = excluded.{c}' for c in columns)
    differs = ' OR '.join(f'order_list_entries.{c} <> excluded.{c}' for c in columns)
    upsert = f"""INSERT INTO {entries} ({all_columns}) SELECT {all_columns} FROM {view} WHERE id = NEW.id
                 ON CONFLICT (id) DO UPDATE SET {updates} WHERE {differs}"""
    if postgres:
        run(f"""CREATE FUNCTION {prefix}update_order_list() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF TG_OP <> 'INSERT' AND OLD.kind = 'market_order' AND NEW.kind <> OLD.kind THEN
              DELETE FROM {entries} WHERE id = OLD.id;
            ELSIF NEW.kind = 'market_order' THEN
              {upsert};
            END IF;
            RETURN NULL;
          END $$""")
        run(f"""CREATE TRIGGER update_order_list AFTER INSERT OR UPDATE OF kind, data ON {entities}
                 FOR EACH ROW EXECUTE FUNCTION {prefix}update_order_list()""")
    else:
        for action in ('INSERT', 'UPDATE OF kind, data'):
            label = action.split()[0].lower()
            run(f"""CREATE TRIGGER order_list_{label} AFTER {action} ON {entities}
                     WHEN NEW.kind = 'market_order' BEGIN {upsert}; END""")
        run(f"""CREATE TRIGGER order_list_reclassify AFTER UPDATE OF kind ON {entities}
                 WHEN OLD.kind='market_order' AND NEW.kind<>OLD.kind
                 BEGIN DELETE FROM {entries} WHERE id=OLD.id; END""")
        # SQLite connections can have foreign-key enforcement disabled.
        run(f"""CREATE TRIGGER order_list_delete AFTER DELETE ON {entities} WHEN OLD.kind='market_order'
                 BEGIN DELETE FROM {entries} WHERE id=OLD.id; END""")
    run(f'INSERT INTO {entries} ({all_columns}) SELECT {all_columns} FROM {view}')
