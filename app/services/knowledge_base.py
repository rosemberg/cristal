import json
import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    Base de conhecimento do portal de Transparência do TRE-PI.
    Usa SQLite com FTS5 para busca full-text eficiente.
    """

    def __init__(self, db_path: str = None) -> None:
        """
        Inicializa a conexão com o banco de dados.

        Args:
            db_path: Caminho para o arquivo knowledge.db.
                     Padrão: app/data/knowledge.db relativo a este módulo.
        """
        if db_path is None:
            db_path = str(Path(__file__).parent.parent / "data" / "knowledge.db")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        logger.info("KnowledgeBase conectada: %s", db_path)
        self._ensure_fts_ready()

    def _ensure_fts_ready(self) -> None:
        """
        Garante que o índice FTS5 está funcional.
        Corrige automaticamente o mismatch entre a coluna 'tags' da FTS5
        e a coluna 'tags_json' da tabela pages (bug do crawler original).
        """
        try:
            self.conn.execute("SELECT rowid FROM pages_fts WHERE pages_fts MATCH 'test' LIMIT 1")
        except sqlite3.OperationalError as exc:
            if "no such column" in str(exc).lower():
                logger.warning(
                    "Índice FTS5 com schema inválido (%s). Corrigindo...", exc
                )
                try:
                    self.conn.execute('ALTER TABLE pages ADD COLUMN tags TEXT DEFAULT ""')
                except sqlite3.OperationalError:
                    pass  # Coluna já existe
                self.conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('rebuild')")
                self.conn.commit()
                logger.info("Índice FTS5 reconstruído com sucesso.")
            else:
                logger.warning("Erro ao verificar FTS5: %s", exc)

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Converte uma linha do banco para dict, parseando campos JSON."""
        d = dict(row)
        try:
            d["breadcrumb"] = json.loads(d.get("breadcrumb_json", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["breadcrumb"] = []
        try:
            d["tags"] = json.loads(d.get("tags_json", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
        return d

    def _get_documents_for_page(self, page_url: str) -> list[dict]:
        """Retorna documentos (PDFs, CSVs, etc.) associados a uma página."""
        cursor = self.conn.execute(
            "SELECT document_url, document_title, document_type "
            "FROM documents WHERE page_url = ?",
            (page_url,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _build_fts_query(self, query: str) -> str:
        """
        Constrói query FTS5 a partir do texto do usuário.
        Remove caracteres especiais do FTS5, tokeniza em palavras
        e conecta com OR para busca ampla.
        """
        clean = re.sub(r'[^\w\s]', ' ', query, flags=re.UNICODE)
        words = [w.strip() for w in clean.split() if len(w.strip()) >= 2]
        if not words:
            return query
        return " OR ".join(words)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Busca páginas relevantes usando FTS5 com ranking BM25.

        Retorna lista de dicts com: url, title, description, content_summary,
        main_content, category, subcategory, content_type, breadcrumb, documents.
        """
        fts_query = self._build_fts_query(query)
        try:
            cursor = self.conn.execute(
                """
                SELECT p.id, p.url, p.title, p.description, p.content_summary,
                       p.main_content, p.category, p.subcategory, p.content_type,
                       p.breadcrumb_json, p.depth, rank
                FROM pages_fts
                JOIN pages p ON pages_fts.rowid = p.id
                WHERE pages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, top_k),
            )
            rows = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning(
                "Erro na busca FTS5 '%s': %s — usando fallback LIKE", query, exc
            )
            return self._search_fallback(query, top_k)

        results = []
        for row in rows:
            d = self._row_to_dict(row)
            d["documents"] = self._get_documents_for_page(d["url"])
            results.append(d)

        logger.debug("Busca FTS5 '%s' retornou %d resultados", query, len(results))
        return results

    def _search_fallback(self, query: str, top_k: int) -> list[dict]:
        """Busca LIKE simples como fallback quando FTS5 não está disponível."""
        like_q = f"%{query}%"
        cursor = self.conn.execute(
            """
            SELECT id, url, title, description, content_summary, main_content,
                   category, subcategory, content_type, breadcrumb_json, depth
            FROM pages
            WHERE search_text LIKE ?
            LIMIT ?
            """,
            (like_q, top_k),
        )
        results = []
        for row in cursor.fetchall():
            d = self._row_to_dict(row)
            d["documents"] = self._get_documents_for_page(d["url"])
            results.append(d)
        return results

    def get_page_with_context(self, url: str) -> dict | None:
        """
        Retorna uma página específica com contexto completo:
        - Dados da página (title, description, content_summary, main_content)
        - Documentos associados (PDFs, CSVs)
        - Links filhos (subpáginas da árvore de navegação)
        - Breadcrumb
        """
        cursor = self.conn.execute(
            """
            SELECT id, url, title, description, content_summary, main_content,
                   category, subcategory, content_type, breadcrumb_json,
                   depth, parent_url
            FROM pages WHERE url = ?
            """,
            (url,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        d = self._row_to_dict(row)
        d["documents"] = self._get_documents_for_page(url)

        children_cursor = self.conn.execute(
            "SELECT child_url, child_title FROM navigation_tree "
            "WHERE parent_url = ? ORDER BY sort_order",
            (url,),
        )
        d["children"] = [dict(r) for r in children_cursor.fetchall()]

        return d

    def get_categories(self) -> list[dict]:
        """
        Retorna lista de categorias com contagem de páginas, ordenadas por popularidade.
        Exemplo: [{"category": "Gestão de Pessoas", "count": 45}, ...]
        """
        cursor = self.conn.execute(
            """
            SELECT category, COUNT(*) as count
            FROM pages
            WHERE category != ''
            GROUP BY category
            ORDER BY count DESC
            """
        )
        return [{"category": row["category"], "count": row["count"]} for row in cursor.fetchall()]

    def get_suggestions(self) -> list[str]:
        """
        Retorna sugestões de perguntas frequentes baseadas
        nas categorias mais populares da base de conhecimento.
        """
        _CATEGORY_SUGGESTIONS: dict[str, str] = {
            "Gestão de Pessoas": "Remuneração dos servidores",
            "Licitações, Contratos e Instrumentos de Cooperação": "Licitações em andamento",
            "Licitações e Contratos": "Licitações em andamento",
            "Orçamento e Finanças": "Execução orçamentária",
            "Prestação de Contas": "Relatório de gestão",
            "LGPD": "LGPD e proteção de dados",
            "Tecnologia da Informação": "Contratos de TI",
            "Auditoria": "Plano de auditoria interna",
            "Dados Abertos": "Dados abertos disponíveis",
            "Acesso à Informação": "Serviço de Informação ao Cidadão",
            "Responsabilidade Socioambiental": "Ações socioambientais",
            "Gestão e Governança": "Estrutura organizacional",
        }
        _FALLBACK = [
            "Licitações em andamento",
            "Remuneração dos servidores",
            "Relatório de gestão",
            "LGPD e proteção de dados",
            "Contratos de TI",
            "Prestação de contas",
        ]

        categories = self.get_categories()
        suggestions: list[str] = []
        seen: set[str] = set()

        for cat_data in categories:
            suggestion = _CATEGORY_SUGGESTIONS.get(cat_data["category"])
            if suggestion and suggestion not in seen:
                suggestions.append(suggestion)
                seen.add(suggestion)
            if len(suggestions) >= 6:
                break

        return suggestions if suggestions else _FALLBACK

    def get_stats(self) -> dict:
        """
        Retorna estatísticas da base de conhecimento para o endpoint /api/health.
        Inclui total de páginas, documentos, categorias e links.
        """
        def _count(sql: str) -> int:
            row = self.conn.execute(sql).fetchone()
            return row[0] if row else 0

        return {
            "total_pages": _count("SELECT COUNT(*) FROM pages"),
            "total_documents": _count("SELECT COUNT(*) FROM documents"),
            "total_categories": _count(
                "SELECT COUNT(DISTINCT category) FROM pages WHERE category != ''"
            ),
            "total_links": _count("SELECT COUNT(*) FROM page_links"),
        }
