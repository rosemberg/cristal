# Plano de Implementação — Assistente de IA para Transparência e Prestação de Contas do TRE-PI

**Projeto:** TRE-PI Transparência Chat  
**Data:** 2026-03-12  
**Versão:** 1.0  
**LLM:** Gemini 2.5 Flash Lite via Vertex AI  
**Referência visual:** Interface estilo "PJe Chat" (chat conversacional institucional)

---

## 1. Visão Geral do Projeto

### 1.1 Objetivo

Criar um assistente conversacional web (chatbot) que permita ao cidadão consultar informações da seção de **Transparência e Prestação de Contas** do TRE-PI de forma intuitiva. O assistente deve:

- Receber perguntas em linguagem natural
- Identificar o tema e localizar a informação no mapa de 298 páginas e 18 documentos do portal
- Fornecer links diretos para as páginas relevantes
- Quando possível, navegar e extrair o conteúdo da página para exibir ao usuário no próprio chat
- Para conteúdos longos ou mídias (PDF, áudio, vídeo), informar apenas o link com instruções de acesso
- Ter visual elegante e institucional, inspirado no design do "PJe Chat"

### 1.2 Stack Tecnológica

| Componente | Tecnologia |
|---|---|
| **Frontend** | HTML5 + CSS3 + JavaScript (vanilla, single-page) |
| **Backend** | Python (FastAPI) |
| **LLM** | Gemini 2.5 Flash Lite via Vertex AI |
| **Base de Conhecimento** | Arquivo SKILL.md (JSON/YAML estruturado em memória) |
| **Web Scraping** | `httpx` + `BeautifulSoup4` (para navegação e extração de conteúdo) |
| **Deploy** | OpenShift 4.x (on-premise TRE-PI) |
| **Autenticação GCP** | Service Account com Workload Identity ou JSON key |

---

## 2. Arquitetura do Sistema

### 2.1 Diagrama de Componentes

```
┌─────────────────────────────────────────────────────────┐
│                    FRONTEND (HTML/CSS/JS)                │
│  ┌───────────────────────────────────────────────────┐  │
│  │              Interface de Chat                     │  │
│  │  - Barra superior institucional TRE-PI            │  │
│  │  - Área de mensagens (bot + usuário)              │  │
│  │  - Campo de input + botão enviar                  │  │
│  │  - Área de "Decisão Simplificada" / Resposta      │  │
│  │  - Accordions para conteúdo expandível            │  │
│  │  - Links clicáveis com ícones                     │  │
│  │  - Sugestões rápidas (chips clicáveis)            │  │
│  └───────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP POST /api/chat
                       │ HTTP GET  /api/health
                       ▼
┌─────────────────────────────────────────────────────────┐
│                   BACKEND (FastAPI)                      │
│  ┌─────────────────┐  ┌──────────────────────────────┐  │
│  │  Router /api     │  │   KnowledgeBase Manager      │  │
│  │  - POST /chat    │  │   - Carrega SKILL.md         │  │
│  │  - GET /health   │  │   - Indexa páginas por tema   │  │
│  │  - GET /suggest  │  │   - Busca semântica simples   │  │
│  └────────┬────────┘  └──────────────┬───────────────┘  │
│           │                          │                   │
│  ┌────────▼──────────────────────────▼───────────────┐  │
│  │              Chat Engine                           │  │
│  │  - Monta prompt com contexto da knowledge base    │  │
│  │  - Envia para Vertex AI (Gemini 2.5 Flash Lite)   │  │
│  │  - Processa resposta e formata para o frontend     │  │
│  │  - Decide: exibir conteúdo ou apenas link          │  │
│  └────────┬──────────────────────────────────────────┘  │
│           │                                              │
│  ┌────────▼──────────────────────────────────────────┐  │
│  │           Content Fetcher (Web Scraper)            │  │
│  │  - Navega até a URL do tre-pi.jus.br              │  │
│  │  - Extrai texto principal da página               │  │
│  │  - Detecta tipo de conteúdo (HTML, PDF, vídeo)    │  │
│  │  - Retorna texto limpo ou flag de mídia            │  │
│  └────────┬──────────────────────────────────────────┘  │
│           │                                              │
│  ┌────────▼──────────────────────────────────────────┐  │
│  │         Vertex AI Client                           │  │
│  │  - google-cloud-aiplatform SDK                    │  │
│  │  - Autenticação via Service Account               │  │
│  │  - Modelo: gemini-2.5-flash-lite                  │  │
│  │  - Streaming response                             │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Fluxo de Dados

1. **Usuário digita pergunta** → Frontend envia `POST /api/chat` com `{ "message": "...", "history": [...] }`
2. **Backend recebe** → KnowledgeBase busca páginas/documentos relevantes pelo tema
3. **Chat Engine** monta o prompt do sistema incluindo:
   - Instruções de comportamento do assistente
   - Páginas relevantes encontradas (URL + descrição)
   - Histórico de conversa (últimas N mensagens)
   - Pergunta do usuário
4. **Content Fetcher** (quando necessário) navega até a URL e extrai texto
5. **Vertex AI** recebe o prompt e gera a resposta
6. **Backend formata** a resposta em JSON estruturado com:
   - Texto da resposta
   - Links relevantes (com título e URL)
   - Flag indicando se há conteúdo expandível
   - Conteúdo extraído da página (se aplicável)
7. **Frontend renderiza** no chat com formatação visual apropriada

---

## 3. Detalhamento do Backend

### 3.1 Estrutura de Pastas

```
tre-pi-transparencia-chat/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, CORS, static files
│   ├── config.py               # Configurações (env vars, GCP project)
│   ├── routers/
│   │   ├── __init__.py
│   │   └── chat.py             # Endpoints /api/chat, /api/health, /api/suggest
│   ├── services/
│   │   ├── __init__.py
│   │   ├── knowledge_base.py   # Carrega e indexa SKILL.md
│   │   ├── chat_engine.py      # Orquestra prompt + LLM + resposta
│   │   ├── content_fetcher.py  # Web scraping do tre-pi.jus.br
│   │   └── vertex_client.py    # Cliente Vertex AI / Gemini
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py          # Pydantic models (ChatRequest, ChatResponse, etc.)
│   ├── prompts/
│   │   └── system_prompt.py    # Template do prompt de sistema
│   └── data/
│       └── knowledge.md        # SKILL.md (base de conhecimento)
├── static/                     # Frontend (HTML, CSS, JS)
│   ├── index.html
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   └── chat.js
│   └── assets/
│       ├── logo-trepi.svg
│       ├── bot-avatar.svg
│       └── user-avatar.svg
├── Containerfile               # Para build no OpenShift
├── requirements.txt
├── .env.example
└── README.md
```

### 3.2 Módulo: `knowledge_base.py`

**Responsabilidades:**

- Carregar o SKILL.md na inicialização do servidor
- Parsear o Markdown e criar um índice em memória com a estrutura:

```python
@dataclass
class PageEntry:
    title: str
    url: str
    description: str
    category: str          # Ex: "Licitações", "Gestão de Pessoas", "Auditoria"
    keywords: list[str]    # Palavras-chave extraídas do título e descrição
    content_type: str      # "page", "pdf", "csv", "video", "audio", "external"

@dataclass
class KnowledgeBase:
    pages: list[PageEntry]
    categories: dict[str, list[PageEntry]]  # Agrupado por categoria
    
    def search(self, query: str, top_k: int = 5) -> list[PageEntry]:
        """Busca por similaridade textual simples (TF-IDF ou keyword matching)."""
        ...
    
    def get_by_category(self, category: str) -> list[PageEntry]:
        """Retorna todas as páginas de uma categoria."""
        ...
```

**Estratégia de busca:** Usar busca por keywords com pontuação por relevância. Não é necessário embedding vetorial pois o Gemini já fará a interpretação semântica — a busca serve para filtrar o contexto enviado ao LLM.

**Categorização automática:** Derivar a categoria pela estrutura da URL. Exemplo:
- `/gestao-de-pessoas/...` → "Gestão de Pessoas"
- `/licitacoes-e-contratos/...` → "Licitações e Contratos"
- `/planos-de-auditoria-interna/...` → "Auditoria"
- `/governanca/...` → "Gestão e Governança"

**Detecção de tipo de conteúdo:**
- URLs terminando em `.pdf` → `content_type = "pdf"`
- URLs terminando em `.csv` → `content_type = "csv"`
- URLs contendo `youtube` ou `transmissao-ao-vivo` → `content_type = "video"`
- URLs contendo `swagger` → `content_type = "api"`
- Demais → `content_type = "page"`

### 3.3 Módulo: `content_fetcher.py`

**Responsabilidades:**

- Receber uma URL do domínio `tre-pi.jus.br`
- Fazer request HTTP e extrair o conteúdo textual principal da página
- Retornar texto limpo (sem navegação, rodapé, sidebar)
- Detectar se o conteúdo é acessível (HTML) ou se é mídia/documento

```python
class ContentFetcher:
    ALLOWED_DOMAIN = "tre-pi.jus.br"
    MAX_CONTENT_LENGTH = 3000  # caracteres máximos para exibir inline
    
    async def fetch_page_content(self, url: str) -> FetchResult:
        """
        Retorna:
        - FetchResult com texto extraído se for página HTML curta
        - FetchResult com flag media=True se for PDF/vídeo/etc
        - FetchResult com texto truncado + link se for muito longo
        """
        ...
    
    def _extract_main_content(self, html: str) -> str:
        """Remove nav, footer, sidebar. Extrai <main> ou #content."""
        ...
    
    def _detect_content_type(self, url: str, headers: dict) -> str:
        """Verifica Content-Type e extensão da URL."""
        ...
```

**Regras de decisão:**

| Condição | Ação |
|---|---|
| Página HTML com conteúdo ≤ 3000 chars | Extrair e exibir no chat |
| Página HTML com conteúdo > 3000 chars | Exibir resumo + link "Ver mais" |
| Arquivo PDF | Apenas link + instrução "Clique para baixar o PDF" |
| Arquivo CSV | Apenas link + instrução "Clique para baixar a planilha" |
| Vídeo/YouTube | Apenas link + instrução "Clique para assistir" |
| Página do Swagger/API | Apenas link + instrução de acesso |
| Erro HTTP ou timeout | Informar link direto como fallback |

**Importante:** Implementar cache (TTL de 1 hora) para evitar requests repetidos ao site do TRE-PI. Usar `httpx.AsyncClient` com timeout de 10 segundos.

### 3.4 Módulo: `vertex_client.py`

**Responsabilidades:**

- Inicializar cliente Vertex AI com credenciais de Service Account
- Enviar prompts para o Gemini 2.5 Flash Lite
- Suportar streaming de resposta

```python
from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel, Content, Part

class VertexClient:
    def __init__(self, project_id: str, location: str = "us-central1"):
        aiplatform.init(project=project_id, location=location)
        self.model = GenerativeModel("gemini-2.5-flash-lite")
    
    async def generate(
        self, 
        system_prompt: str, 
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 2048
    ) -> str:
        """Envia para Gemini e retorna resposta."""
        ...
    
    async def generate_stream(
        self, 
        system_prompt: str, 
        messages: list[dict]
    ) -> AsyncIterator[str]:
        """Streaming para resposta em tempo real."""
        ...
```

**Configuração de autenticação no OpenShift:**

```bash
# Opção 1: Service Account Key (recomendado para on-premise)
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json

# Opção 2: Workload Identity Federation (se disponível)
GOOGLE_CLOUD_PROJECT=tre-pi-project-id
VERTEX_AI_LOCATION=us-central1
```

**Parâmetros do modelo:**
- `temperature: 0.3` (respostas mais objetivas e factuais)
- `max_output_tokens: 2048`
- `top_p: 0.8`
- `top_k: 40`

### 3.5 Módulo: `chat_engine.py`

**Responsabilidades:**

- Orquestrar todo o fluxo: busca na knowledge base → montagem do prompt → chamada ao LLM → pós-processamento
- Decidir quando buscar conteúdo da página vs. apenas retornar link
- Formatar a resposta para o frontend

```python
class ChatEngine:
    def __init__(self, kb: KnowledgeBase, fetcher: ContentFetcher, vertex: VertexClient):
        self.kb = kb
        self.fetcher = fetcher
        self.vertex = vertex
    
    async def process_message(self, message: str, history: list[dict]) -> ChatResponse:
        # 1. Buscar páginas relevantes na knowledge base
        relevant_pages = self.kb.search(message, top_k=5)
        
        # 2. Para a página mais relevante, tentar buscar conteúdo
        fetched_content = None
        if relevant_pages and self._should_fetch_content(message, relevant_pages[0]):
            fetched_content = await self.fetcher.fetch_page_content(relevant_pages[0].url)
        
        # 3. Montar prompt com contexto
        system_prompt = self._build_system_prompt(relevant_pages, fetched_content)
        
        # 4. Chamar Gemini
        response_text = await self.vertex.generate(system_prompt, history + [{"role": "user", "content": message}])
        
        # 5. Pós-processar e estruturar resposta
        return self._format_response(response_text, relevant_pages, fetched_content)
```

### 3.6 Módulo: `system_prompt.py`

**O system prompt é crítico.** Deve instruir o Gemini sobre:

```python
SYSTEM_PROMPT_TEMPLATE = """
Você é o assistente virtual do portal de Transparência e Prestação de Contas do 
Tribunal Regional Eleitoral do Piauí (TRE-PI). Seu nome é "Transparência Chat".

## Seu papel
- Ajudar cidadãos a encontrar informações no portal de Transparência do TRE-PI
- Responder de forma clara, objetiva e em linguagem acessível
- Sempre fornecer links diretos para as páginas relevantes
- Quando o conteúdo da página estiver disponível, resumi-lo para o usuário
- Quando for PDF, vídeo, planilha ou outro arquivo, informar o link e explicar como acessar

## Regras de resposta
1. SEMPRE inclua pelo menos um link relevante na resposta
2. Use linguagem simples e acessível (evite jargão jurídico desnecessário)
3. Se não souber a resposta, indique o SIC (Serviço de Informação ao Cidadão)
4. Para documentos PDF: diga "Você pode acessar o documento em PDF clicando no link abaixo"
5. Para vídeos: diga "O vídeo está disponível no link abaixo"
6. Forneça contexto sobre o que o usuário encontrará ao clicar no link
7. Se a pergunta for sobre múltiplos temas, organize por tópicos

## Formato da resposta (JSON)
Responda SEMPRE em JSON com a seguinte estrutura:
{{
  "text": "Texto principal da resposta em Markdown (sem links — use a seção links para isso)",
  "links": [
    {{"title": "Título descritivo", "url": "https://...", "type": "page|pdf|csv|video|external"}}
  ],
  "extracted_content": "Conteúdo extraído da página (quando disponível, máximo 500 palavras)" | null,
  "suggestions": ["Sugestão 1 para próxima pergunta", "Sugestão 2"]
}}

## Páginas relevantes para esta consulta
{relevant_pages}

## Conteúdo extraído (quando disponível)
{fetched_content}

## URL base do portal
https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas
"""
```

### 3.7 Endpoints da API

```python
# POST /api/chat
class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": "..."}]

class LinkItem(BaseModel):
    title: str
    url: str
    type: str  # "page", "pdf", "csv", "video", "external"

class ChatResponse(BaseModel):
    text: str                          # Resposta formatada em Markdown
    links: list[LinkItem] = []         # Links relevantes
    extracted_content: str | None       # Conteúdo extraído da página
    suggestions: list[str] = []        # Sugestões de próximas perguntas
    category: str | None               # Categoria identificada

# GET /api/suggest
# Retorna sugestões iniciais de perguntas
class SuggestResponse(BaseModel):
    suggestions: list[str]

# GET /api/health
# Health check para readiness/liveness probes do OpenShift
```

---

## 4. Detalhamento do Frontend

### 4.1 Referência Visual

O design deve seguir o padrão visual da imagem de referência "PJe Chat" com as seguintes características:

**Paleta de cores institucional TRE-PI:**
- **Primária (header/destaques):** `#006B5F` (verde escuro institucional TRE-PI)
- **Primária hover:** `#005A50`
- **Secundária (accent):** `#00897B` (teal médio)
- **Background principal:** `#F5F5F5` (cinza claro)
- **Background chat:** `#FFFFFF`
- **Background mensagem bot:** `#FFFFFF` com borda `#E0E0E0`
- **Background mensagem usuário:** `#006B5F` com texto branco
- **Background conteúdo extraído:** `#F1F8E9` (verde muito claro, como na referência)
- **Borda conteúdo extraído:** `#C5E1A5` (verde claro)
- **Texto principal:** `#212121`
- **Texto secundário:** `#757575`
- **Links:** `#00897B`

### 4.2 Layout e Componentes

```
┌──────────────────────────────────────────────────────────┐
│  ████  Tribunal Regional Eleitoral do Piauí              │  ← Header verde escuro
├──────────────────────────────────────────────────────────┤
│  🏛️  CONSULTA PÚBLICA DE TRANSPARÊNCIA                  │  ← Título com ícone
│                                                          │
│  🏠 / Transparência / Consultar                          │  ← Breadcrumb
│  Transparência Chat                                      │  ← Nome do chat (teal)
├──────────────────────────────────────────────────────────┤
│                                                          │
│  [🏛️] ┌──────────────────────────────────────────┐      │
│        │ Olá! Sou o Transparência Chat do TRE-PI. │      │  ← Mensagem inicial do bot
│        │ Pergunte sobre qualquer assunto de        │      │
│        │ transparência e prestação de contas.      │      │
│        │                                           │      │
│        │ Exemplos:                                 │      │
│        │ [Licitações em andamento]  [Remuneração]  │      │  ← Chips clicáveis
│        │ [Relatório de Gestão]  [LGPD]             │      │
│        └──────────────────────────────────────────┘      │
│                                                          │
│         ┌──────────────────────────────────────┐  [👤]   │
│         │ Onde encontro as licitações?         │         │  ← Mensagem do usuário
│         └──────────────────────────────────────┘         │
│                                                          │
│  [🏛️] ┌──────────────────────────────────────────┐      │
│        │ 📋 RESPOSTA                              │      │  ← Card de resposta
│        │                                          │      │
│        │ ┌────────────────────────────────────┐   │      │
│        │ │ As licitações do TRE-PI estão...   │   │      │  ← Conteúdo (bg verde claro)
│        │ │ ...                                │   │      │
│        │ └────────────────────────────────────┘   │      │
│        │                                          │      │
│        │ 🔗 Links úteis:                          │      │
│        │  • Pregões em andamento ↗               │      │  ← Links clicáveis
│        │  • Licitações concluídas ↗              │      │
│        │  • Contratos vigentes ↗                 │      │
│        │                                          │      │
│        │ 📄 Ver conteúdo da página       ▼        │      │  ← Accordion expandível
│        │                                          │      │
│        │ ┌────────────────────────────────────┐   │      │
│        │ │ [Atas de registro] [Contratos TI]  │   │      │  ← Sugestões
│        │ └────────────────────────────────────┘   │      │
│        └──────────────────────────────────────────┘      │
│                                                          │
├──────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────┐  [▶️]    │
│  │ Digite sua pergunta...                     │         │  ← Input com botão enviar
│  └────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────┘
```

### 4.3 Componentes HTML/CSS

**4.3.1 Header institucional**
- Barra fixa no topo, fundo `#006B5F`, texto branco
- Logo do TRE-PI à esquerda (SVG)
- Texto "Tribunal Regional Eleitoral do Piauí"

**4.3.2 Área de título**
- Ícone de prédio institucional (🏛️) + "CONSULTA PÚBLICA DE TRANSPARÊNCIA" em caps
- Breadcrumb com links de navegação
- Nome "Transparência Chat" em cor teal (`#00897B`), fonte grande

**4.3.3 Mensagens do bot**
- Avatar circular com ícone institucional (⚖️ ou brasão simplificado) em fundo `#006B5F`
- Card branco com sombra sutil (`box-shadow: 0 1px 3px rgba(0,0,0,0.1)`)
- Borda lateral esquerda verde sutil (opcional)
- Título em caps bold verde ("RESPOSTA" ou categoria)
- Área de conteúdo principal com fundo `#F1F8E9` e borda `#C5E1A5` (como na referência)
- Seção de links com ícone 🔗 e links em teal
- Accordion "Ver conteúdo completo" quando houver conteúdo longo extraído
- Chips de sugestão ao final

**4.3.4 Mensagens do usuário**
- Alinhadas à direita
- Fundo `#006B5F`, texto branco, border-radius arredondado
- Avatar circular com ícone de pessoa em fundo `#006B5F`

**4.3.5 Input de mensagem**
- Campo de texto com placeholder "Digite sua pergunta..."
- Botão circular de envio com ícone de avião de papel (▶️/✈️) em cor `#006B5F`
- Position fixed na parte inferior
- Border-radius suave, sombra sutil

**4.3.6 Chips de sugestão**
- Botões arredondados com borda verde, texto teal
- Ao clicar, enviam a pergunta sugerida
- Hover com fundo verde claro

**4.3.7 Indicador de carregamento**
- Animação de três pontos pulsando (typing indicator)
- Dentro de um card de mensagem do bot

**4.3.8 Card de link para mídia**
- Ícone diferente por tipo: 📄 PDF, 📊 CSV, 🎬 Vídeo, 🔗 Página
- Texto explicativo de como acessar
- Botão/link de download ou acesso direto

### 4.4 JavaScript (`chat.js`)

**Responsabilidades:**

```javascript
// Estado da aplicação
const state = {
    history: [],        // Histórico de mensagens para enviar ao backend
    isLoading: false    // Flag para evitar envios duplicados
};

// Funções principais
async function sendMessage(text) {
    // 1. Adicionar mensagem do usuário ao chat
    // 2. Mostrar indicador de carregamento
    // 3. POST /api/chat com message + history
    // 4. Processar resposta JSON
    // 5. Renderizar mensagem do bot com:
    //    - Texto principal (Markdown → HTML com marked.js ou similar)
    //    - Links como cards clicáveis (target="_blank")
    //    - Conteúdo extraído em accordion (se houver)
    //    - Sugestões como chips
    // 6. Atualizar history
    // 7. Scroll suave para a nova mensagem
}

function renderBotMessage(response) {
    // Cria o card completo da resposta seguindo o design
    // - Header com ícone e título da categoria
    // - Corpo com texto em caixa verde clara
    // - Lista de links com ícones por tipo
    // - Accordion para conteúdo expandível
    // - Chips de sugestão clicáveis
}

function renderLink(link) {
    // Renderiza link com ícone diferente por tipo:
    // page → 🔗, pdf → 📄, csv → 📊, video → 🎬, external → ↗️
}

function renderSuggestionChips(suggestions) {
    // Chips clicáveis que disparam sendMessage(suggestion)
}

// Markdown simples → HTML (negrito, itálico, listas)
function markdownToHtml(text) { ... }

// Inicialização
document.addEventListener('DOMContentLoaded', () => {
    // Mostrar mensagem de boas-vindas
    // Carregar sugestões iniciais via GET /api/suggest
    // Configurar event listeners (Enter, botão enviar, chips)
});
```

### 4.5 Responsividade

- **Desktop (>768px):** Chat centralizado com max-width de 900px, padding lateral
- **Mobile (<768px):** Full-width, input fixo na parte inferior, fontes menores
- **Área de chat:** `calc(100vh - header - input)` com overflow-y scroll

---

## 5. Prompt Engineering

### 5.1 Estratégia de Contexto

O prompt enviado ao Gemini deve seguir esta estrutura:

```
[SYSTEM PROMPT]
├── Identidade e papel do assistente
├── Regras de resposta e formatação
├── Formato JSON esperado
├── Páginas relevantes (top 5 da busca no SKILL.md)
│   └── Para cada: título, URL, descrição, tipo
├── Conteúdo extraído da página (quando disponível)
└── URL base do portal

[HISTÓRICO]
├── user: "mensagem anterior"
├── assistant: "resposta anterior"
└── ... (últimas 6 mensagens)

[MENSAGEM ATUAL]
└── user: "pergunta do usuário"
```

### 5.2 Controle de Token

- **System prompt base:** ~800 tokens
- **Contexto de páginas (5 páginas):** ~500 tokens
- **Conteúdo extraído:** máximo ~1000 tokens (truncar se necessário)
- **Histórico:** últimas 6 mensagens (~600 tokens)
- **Pergunta do usuário:** ~100 tokens
- **Total input estimado:** ~3000 tokens
- **Output máximo:** 2048 tokens

### 5.3 Sugestões Iniciais

Ao carregar o chat, exibir chips com perguntas frequentes:
- "Licitações em andamento"
- "Remuneração dos servidores"
- "Relatório de gestão"
- "LGPD e proteção de dados"
- "Contratos de TI"
- "Prestação de contas"

---

## 6. Tratamento de Tipos de Conteúdo

### 6.1 Matriz de Decisão

| Tipo detectado | Ação no backend | Exibição no frontend |
|---|---|---|
| **Página HTML curta** (≤3000 chars) | Extrair texto com BeautifulSoup | Exibir resumo + conteúdo em accordion |
| **Página HTML longa** (>3000 chars) | Extrair primeiros 3000 chars | Exibir resumo + link "Ver mais no site" |
| **PDF** | Não extrair | Card com 📄 ícone + link de download + instrução |
| **CSV** | Não extrair | Card com 📊 ícone + link de download + instrução |
| **Vídeo/YouTube** | Não extrair | Card com 🎬 ícone + link + instrução "Assistir" |
| **Swagger/API** | Não extrair | Card com 🔧 ícone + link + instrução de acesso |
| **Erro/Timeout** | Retornar URL como fallback | Exibir link direto com mensagem de contingência |

### 6.2 Mensagens por Tipo

**PDF:**
> "O documento está disponível em formato PDF. Clique no link abaixo para visualizar ou baixar:
> 📄 [Nome do documento](URL)
> Dica: O PDF será aberto em uma nova aba do seu navegador."

**Vídeo:**
> "O vídeo da sessão está disponível no link abaixo:
> 🎬 [Título do vídeo](URL)
> Dica: O vídeo será reproduzido no YouTube em uma nova aba."

**CSV:**
> "A planilha de dados está disponível para download:
> 📊 [Nome do arquivo](URL)
> Dica: Você pode abrir o arquivo CSV no Excel ou Google Planilhas."

---

## 7. Deploy no OpenShift

### 7.1 Containerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/

# Service Account key (montar como Secret)
ENV GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa-key.json
ENV VERTEX_PROJECT_ID=tre-pi-project
ENV VERTEX_LOCATION=us-central1
ENV PORT=8080

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 7.2 Configuração OpenShift

- **Secret:** Montar a chave JSON do Service Account como volume em `/secrets/`
- **ConfigMap:** Variáveis de ambiente (project_id, location, etc.)
- **Route:** Expor via HTTPS com TLS termination
- **Probes:**
  - Liveness: `GET /api/health` (timeout 5s, period 30s)
  - Readiness: `GET /api/health` (timeout 3s, period 10s)
- **Resources:**
  - Requests: 256Mi RAM, 250m CPU
  - Limits: 512Mi RAM, 500m CPU

### 7.3 Requirements.txt

```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
httpx>=0.27.0
beautifulsoup4>=4.12.0
google-cloud-aiplatform>=1.72.0
vertexai>=1.72.0
pydantic>=2.9.0
cachetools>=5.5.0
python-dotenv>=1.0.0
```

---

## 8. Testes

### 8.1 Cenários de Teste Prioritários

1. **Pergunta direta sobre licitações** → Deve retornar links da seção de licitações com descrição
2. **Pergunta sobre remuneração** → Deve retornar link da folha de pagamento + explicar que é PDF
3. **Pergunta sobre LGPD** → Deve retornar link da seção LGPD + aviso de privacidade
4. **Pergunta vaga ("como funciona?")** → Deve pedir mais contexto ou mostrar sugestões
5. **Pergunta fora do escopo** → Deve redirecionar para o SIC ou Ouvidoria
6. **Múltiplas perguntas em sequência** → Manter contexto do histórico
7. **Pergunta sobre vídeo das sessões** → Retornar link do YouTube com instrução
8. **Pergunta sobre documento PDF específico** → Retornar link de download com instrução
9. **Content Fetcher com timeout** → Deve retornar link como fallback sem erro
10. **Vertex AI indisponível** → Mensagem de contingência amigável

### 8.2 Testes Automatizados

- **Unit tests:** `pytest` para `knowledge_base.py`, `content_fetcher.py` (com mocks)
- **Integration tests:** Testar fluxo completo com Vertex AI em staging
- **E2E:** Cypress ou Playwright para testar interface do chat

---

## 9. Cronograma Estimado de Desenvolvimento

| Fase | Descrição | Estimativa |
|---|---|---|
| **1. Setup** | Estrutura do projeto, dependências, Containerfile | 2h |
| **2. Knowledge Base** | Parser do SKILL.md, indexação, busca | 3h |
| **3. Vertex Client** | Integração com Gemini via Vertex AI | 2h |
| **4. Content Fetcher** | Web scraping com cache | 3h |
| **5. Chat Engine** | Orquestração, prompt engineering | 3h |
| **6. API (FastAPI)** | Endpoints, schemas, CORS | 2h |
| **7. Frontend** | HTML/CSS/JS completo com design | 5h |
| **8. Integração** | Conectar front + back, ajustes | 2h |
| **9. Testes** | Cenários de teste, ajustes de prompt | 3h |
| **10. Deploy** | OpenShift config, secrets, route | 2h |
| **Total estimado** | | **~27h** |

---

## 10. Instruções para o Claude Code

### 10.1 Ordem de Implementação

1. **Começar pelo backend:** `config.py` → `schemas.py` → `knowledge_base.py` → `vertex_client.py` → `content_fetcher.py` → `chat_engine.py` → `routers/chat.py` → `main.py`
2. **Depois o frontend:** `index.html` → `style.css` → `chat.js`
3. **Por último:** `Containerfile`, `requirements.txt`, `README.md`

### 10.2 Regras para o Claude Code

- **Tudo em um único `index.html`** para simplificar, com `<style>` e `<script>` inline (alternativa: manter separados em `static/`)
- **O SKILL.md deve ser copiado para `app/data/knowledge.md`** e carregado na inicialização
- **Usar `async/await`** em todo o backend
- **Implementar rate limiting** no endpoint de chat (máximo 10 requests/min por IP)
- **Logs estruturados** com `logging` do Python
- **Não usar frameworks CSS** — CSS puro para máximo controle visual
- **Seguir fielmente a paleta de cores** da seção 4.1
- **Respostas do Gemini em JSON** — implementar fallback com regex se o JSON vier malformado
- **CORS** habilitado apenas para domínio do TRE-PI em produção, `*` em dev
- **O frontend deve funcionar sem JavaScript habilitado** como graceful degradation (mensagem de fallback)

### 10.3 Variáveis de Ambiente

```bash
# .env
VERTEX_PROJECT_ID=tre-pi-gcp-project
VERTEX_LOCATION=us-central1
VERTEX_MODEL=gemini-2.5-flash-lite
GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa-key.json
ALLOWED_ORIGINS=https://www.tre-pi.jus.br
CACHE_TTL_SECONDS=3600
MAX_HISTORY_MESSAGES=6
MAX_CONTENT_LENGTH=3000
RATE_LIMIT_PER_MINUTE=10
LOG_LEVEL=INFO
```

---

## 11. Considerações de Segurança

- **Sanitização de input:** Escapar HTML no frontend antes de renderizar respostas
- **Rate limiting:** Prevenir abuso da API do Vertex AI
- **CORS restrito:** Apenas domínio do TRE-PI em produção
- **Sem dados sensíveis no frontend:** Chaves de API apenas no backend
- **Content Security Policy:** Headers adequados
- **Validação de URL:** Content Fetcher só acessa `tre-pi.jus.br`
- **Timeout:** 15 segundos máximo por request ao Vertex AI

---

## 12. Melhorias Futuras (Backlog)

- Streaming de resposta (SSE) para feedback visual durante geração
- Histórico persistente com `sessionStorage` (ou backend com Redis)
- Analytics de perguntas mais frequentes
- Feedback (👍/👎) nas respostas para melhoria contínua
- Busca vetorial com embeddings para melhor precisão
- Suporte a áudio (speech-to-text) para acessibilidade
- Integração com o PJe Chat existente
- Cache inteligente de respostas para perguntas recorrentes
- Modo escuro
