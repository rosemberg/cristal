"""Utilitários de progresso e formatação visual para CLIs — stdlib only.

Sem dependências externas (sem rich, tqdm, etc.).
Usa sys.stderr para não poluir stdout (que pode ser redirecionado para JSON).
"""

from __future__ import annotations

import shutil
import sys
import time


# ─── Formatação de tempo ───────────────────────────────────────────────────────


def format_duration(seconds: float) -> str:
    """Formata duração em segundos para string legível."""
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


# ─── Barra de progresso ────────────────────────────────────────────────────────


class ProgressBar:
    """Barra de progresso terminal via carriage return (\\r).

    Thread-safe dentro de asyncio (single-thread cooperative scheduling).
    Detecta TTY: em ambientes sem terminal (CI, cron), emite linhas simples
    a cada 10% para não poluir logs com caracteres de controle.

    Exemplo:
        bar = ProgressBar(total=200, prefix="Extração")
        for url in urls:
            await process(url)
            bar.update(current_item=url)
        bar.finish()
    """

    _BAR_CHARS = "█░"
    _BAR_WIDTH = 28

    def __init__(self, total: int, prefix: str = "") -> None:
        self._total = max(total, 1)
        self._current = 0
        self._errors = 0
        self._prefix = prefix
        self._start = time.monotonic()
        self._current_item = ""
        self._is_tty = sys.stderr.isatty()
        self._last_pct_logged = -1  # para modo não-TTY

    @property
    def current(self) -> int:
        return self._current

    @property
    def errors(self) -> int:
        return self._errors

    def update(self, current_item: str = "", *, error: bool = False) -> None:
        """Avança o contador e redesenha a barra."""
        self._current += 1
        if error:
            self._errors += 1
        self._current_item = current_item
        self._render()

    def finish(self) -> None:
        """Finaliza a barra (nova linha)."""
        if self._is_tty:
            sys.stderr.write("\n")
        else:
            elapsed = time.monotonic() - self._start
            sys.stderr.write(
                f"  [{self._prefix}] {self._current}/{self._total} concluído"
                f" em {format_duration(elapsed)}\n"
            )
        sys.stderr.flush()

    def _render(self) -> None:
        if self._is_tty:
            self._render_tty()
        else:
            self._render_plain()

    def _render_tty(self) -> None:
        cols = shutil.get_terminal_size((80, 24)).columns

        pct = self._current / self._total
        filled = int(self._BAR_WIDTH * pct)
        bar = self._BAR_CHARS[0] * filled + self._BAR_CHARS[1] * (self._BAR_WIDTH - filled)

        elapsed = time.monotonic() - self._start
        if self._current > 0:
            eta = elapsed / self._current * (self._total - self._current)
            eta_str = f"ETA {format_duration(eta)}"
        else:
            eta_str = "ETA --"

        err_part = f" err:{self._errors}" if self._errors else ""
        counter = f"{self._current}/{self._total}"
        prefix_part = f"{self._prefix} |{bar}| {counter} {eta_str}{err_part}"

        # Espaço restante para a URL atual
        max_item = cols - len(prefix_part) - 3
        item = self._current_item
        if max_item > 8 and item:
            if len(item) > max_item:
                item = "…" + item[-(max_item - 1):]
        else:
            item = ""

        line = f"\r{prefix_part}  {item}"
        line = line[:cols].ljust(cols)
        sys.stderr.write(line)
        sys.stderr.flush()

    def _render_plain(self) -> None:
        """Modo sem TTY: imprime apenas a cada 10%."""
        pct_int = int(self._current / self._total * 10) * 10
        if pct_int > self._last_pct_logged:
            self._last_pct_logged = pct_int
            elapsed = time.monotonic() - self._start
            sys.stderr.write(
                f"  [{self._prefix}] {pct_int}% — {self._current}/{self._total}"
                f" ({format_duration(elapsed)} decorridos)\n"
            )
            sys.stderr.flush()


# ─── Resumo visual ─────────────────────────────────────────────────────────────


def print_phase(label: str) -> None:
    """Imprime cabeçalho de fase."""
    sys.stderr.write(f"\n  {label}\n")
    sys.stderr.flush()


def print_summary(title: str, metrics: dict[str, object], duration: float) -> None:
    """Imprime bloco de resumo ao final de uma etapa."""
    width = 52
    lines = [
        f"\n{'─' * width}",
        f"  {title}",
        f"{'─' * width}",
    ]
    for k, v in metrics.items():
        lines.append(f"  {k:<28} {v}")
    lines.append(f"  {'Duração':<28} {format_duration(duration)}")
    lines.append(f"{'─' * width}")
    sys.stderr.write("\n".join(lines) + "\n")
    sys.stderr.flush()
