"""Políticas de retry e limite de taxa, centralizadas.

Toda chamada HTTP do serviço passa por aqui: erros transitórios (timeout,
falha de rede, 429 e 5xx) são repetidos com backoff exponencial; os demais
viram ErroPermanente e não são repetidos.
"""

import asyncio
import time
from collections import deque
from collections.abc import Callable

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

TIMEOUT_PADRAO = httpx.Timeout(10.0)


class ErroTransitorio(Exception):
    """Falha que pode passar sozinha: vale tentar de novo."""


class ErroPermanente(Exception):
    """Falha que não muda com nova tentativa (ex.: 400, 401, 404)."""


def verificar_resposta(resposta: httpx.Response) -> httpx.Response:
    """Converte status HTTP de erro nas exceções do serviço."""
    status = resposta.status_code
    if status == 429 or status >= 500:
        raise ErroTransitorio(f"HTTP {status} em {resposta.request.url.path}")
    if status >= 400:
        raise ErroPermanente(f"HTTP {status} em {resposta.request.url.path}")
    return resposta


def politica_http(tentativas: int = 4, espera_max: float = 30.0) -> AsyncRetrying:
    """Retry assíncrono com backoff exponencial e jitter."""
    return AsyncRetrying(
        retry=retry_if_exception_type(
            (ErroTransitorio, httpx.TimeoutException, httpx.TransportError)
        ),
        stop=stop_after_attempt(tentativas),
        wait=wait_exponential_jitter(initial=0.5, max=espera_max),
        reraise=True,
    )


def backoff_evento(tentativa: int, base_segundos: int = 60, max_segundos: int = 3600) -> int:
    """Segundos até a próxima tentativa de um evento: 60, 120, 240... até 1 h."""
    return min(base_segundos * 2 ** max(tentativa - 1, 0), max_segundos)


class LimitadorTaxa:
    """Janela deslizante: no máximo `limite` chamadas a cada `janela` segundos."""

    def __init__(
        self, limite: int, janela: float, relogio: Callable[[], float] = time.monotonic
    ) -> None:
        self.limite = limite
        self.janela = janela
        self._relogio = relogio
        self._chamadas: deque[float] = deque()
        self._trava = asyncio.Lock()

    async def aguardar(self) -> None:
        async with self._trava:
            while True:
                agora = self._relogio()
                while self._chamadas and agora - self._chamadas[0] >= self.janela:
                    self._chamadas.popleft()
                if len(self._chamadas) < self.limite:
                    self._chamadas.append(agora)
                    return
                await asyncio.sleep(self.janela - (agora - self._chamadas[0]))
