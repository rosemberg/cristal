"""
Migra dados do SQLite (knowledge.db) para o PostgreSQL via CSV COPY.

Uso:
    python scripts/migrate_sqlite_to_postgres.py
"""

import csv
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

SQLITE_PATH = Path(__file__).parent.parent / "app" / "data" / "knowledge.db"
PG_CONTAINER = "cristal-db-1"
PG_USER = "cristal"
PG_DB = "cristal"


def psql(sql: str, check: bool = True) -> str:
    """Executa SQL no PostgreSQL via docker exec."""
    result = subprocess.run(
        ["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB],
        input=sql,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        print(f"ERRO: {result.stderr}")
        sys.exit(1)
    return result.stdout


def copy_csv_to_pg(table: str, columns: list[str], rows: list[list]) -> int:
    """Copia dados via \\copy (client-side) com arquivo local montado no container."""
    # Gerar CSV
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline=""
    ) as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)
        csv_path = f.name

    container_csv = f"/tmp/{table}.csv"

    try:
        # Copiar CSV para o container
        subprocess.run(
            ["docker", "cp", csv_path, f"{PG_CONTAINER}:{container_csv}"],
            check=True,
            capture_output=True,
        )

        # Dar permissão de leitura
        subprocess.run(
            ["docker", "exec", PG_CONTAINER, "chmod", "644", container_csv],
            check=True,
            capture_output=True,
        )

        # Usar \copy via psql (client-side, sem problema de permissão do server)
        cols = ", ".join(columns)
        psql_cmd = f"\\copy {table} ({cols}) FROM '{container_csv}' WITH (FORMAT csv, NULL '\\N')"

        result = subprocess.run(
            ["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB, "-c", psql_cmd],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"  ERRO ao copiar para {table}: {result.stderr[:500]}")
            return 0

        # Extrair contagem
        for line in result.stdout.split("\n"):
            if "COPY" in line:
                try:
                    return int(line.split()[-1])
                except (ValueError, IndexError):
                    pass
        return len(rows)
    finally:
        Path(csv_path).unlink(missing_ok=True)
        subprocess.run(
            ["docker", "exec", PG_CONTAINER, "rm", "-f", container_csv],
            capture_output=True,
        )


def null_or_val(val):
    """Retorna \\N para NULL/vazio, senão o valor."""
    if val is None or val == "":
        return "\\N"
    return val


def main():
    if not SQLITE_PATH.exists():
        print(f"ERRO: SQLite não encontrado em {SQLITE_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Limpar tabelas (ordem FK)
    print("Limpando tabelas existentes...")
    psql("""
        DELETE FROM navigation_tree;
        DELETE FROM page_links;
        DELETE FROM documents;
        DELETE FROM document_chunks;
        DELETE FROM document_contents;
        DELETE FROM document_tables;
        DELETE FROM pages;
    """)

    # 1. PAGES
    print("\nMigrando pages...")
    cursor.execute("SELECT * FROM pages ORDER BY id")
    rows = cursor.fetchall()
    csv_rows = []
    for row in rows:
        breadcrumb = row["breadcrumb_json"] if row["breadcrumb_json"] else "[]"
        tags_json = row["tags_json"] if row["tags_json"] else "[]"

        try:
            tags_list = json.loads(tags_json)
            tags_pg = "{" + ",".join(f'"{t}"' for t in tags_list) + "}" if tags_list else "{}"
        except (json.JSONDecodeError, TypeError):
            tags_pg = "{}"

        csv_rows.append([
            row["url"],
            row["title"] or "",
            null_or_val(row["description"]),
            null_or_val(row["main_content"]),
            null_or_val(row["content_summary"]),
            null_or_val(row["category"]),
            null_or_val(row["subcategory"]),
            row["content_type"] or "page",
            row["depth"] if row["depth"] is not None else 0,
            null_or_val(row["parent_url"]),
            breadcrumb,  # jsonb
            tags_pg,  # text[]
            null_or_val(row["last_modified"]),
            row["extracted_at"] if row["extracted_at"] else "\\N",
        ])

    count = copy_csv_to_pg(
        "pages",
        ["url", "title", "description", "main_content", "content_summary",
         "category", "subcategory", "content_type", "depth", "parent_url",
         "breadcrumb", "tags", "last_modified", "extracted_at"],
        csv_rows,
    )
    print(f"  pages: {count} registros importados")

    # 2. DOCUMENTS (deduplicar por page_url + document_url)
    print("Migrando documents...")
    cursor.execute("""
        SELECT page_url, document_url, document_title, document_type, context
        FROM documents
        GROUP BY page_url, document_url
        ORDER BY MIN(id)
    """)
    rows = cursor.fetchall()
    csv_rows = []
    for row in rows:
        csv_rows.append([
            row["page_url"],
            row["document_url"],
            null_or_val(row["document_title"]),
            row["document_type"] or "pdf",
            null_or_val(row["context"]),
        ])

    count = copy_csv_to_pg(
        "documents",
        ["page_url", "document_url", "document_title", "document_type", "context"],
        csv_rows,
    )
    print(f"  documents: {count} registros importados")

    # 3. PAGE_LINKS (deduplicar por source_url + target_url, filtrar por FK)
    print("Migrando page_links...")
    cursor.execute("""
        SELECT source_url, target_url, link_title, link_type
        FROM page_links
        WHERE source_url IN (SELECT url FROM pages)
        GROUP BY source_url, target_url
        ORDER BY MIN(id)
    """)
    rows = cursor.fetchall()
    csv_rows = []
    for row in rows:
        csv_rows.append([
            row["source_url"],
            row["target_url"],
            null_or_val(row["link_title"]),
            row["link_type"] or "internal",
        ])

    count = copy_csv_to_pg(
        "page_links",
        ["source_url", "target_url", "link_title", "link_type"],
        csv_rows,
    )
    print(f"  page_links: {count} registros importados")

    # 4. NAVIGATION_TREE
    print("Migrando navigation_tree...")
    cursor.execute("SELECT * FROM navigation_tree ORDER BY id")
    rows = cursor.fetchall()
    csv_rows = []
    for row in rows:
        csv_rows.append([
            row["parent_url"],
            row["child_url"],
            null_or_val(row["child_title"]),
            row["sort_order"] or 0,
        ])

    count = copy_csv_to_pg(
        "navigation_tree",
        ["parent_url", "child_url", "child_title", "sort_order"],
        csv_rows,
    )
    print(f"  navigation_tree: {count} registros importados")

    conn.close()

    # Verificação final
    print("\n=== Verificação final ===")
    output = psql("""
        SELECT 'pages' as tabela, COUNT(*) as total FROM pages
        UNION ALL SELECT 'documents', COUNT(*) FROM documents
        UNION ALL SELECT 'page_links', COUNT(*) FROM page_links
        UNION ALL SELECT 'navigation_tree', COUNT(*) FROM navigation_tree;
    """)
    print(output)

    # Verificar se search_vector foi populado pelo trigger
    output = psql("SELECT COUNT(*) as com_search_vector FROM pages WHERE search_vector IS NOT NULL;")
    print("Search vectors populados:")
    print(output)

    print("Migração concluída!")


if __name__ == "__main__":
    main()
