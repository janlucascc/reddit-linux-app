# Plano de Implementação UI/UX

## Objetivo

Evoluir o Reddit Linux Desktop para uma experiência centrada na leitura do feed, mantendo a fidelidade estrutural ao Reddit Web e melhorando acessibilidade, estados de interação, densidade visual e adaptação entre resoluções.

## Jornada principal

```text
Abrir o app
  -> escolher Feed ou comunidade
  -> escanear posts
  -> abrir um post
  -> ler e interagir com comentários
  -> voltar ao feed preservando o contexto
```

## Princípios de implementação

1. O feed deve ser o elemento visual dominante.
2. Toda informação visível deve representar estado real ou indicar claramente que é local.
3. Recursos secundários devem usar descoberta progressiva.
4. Toda ação importante precisa funcionar com mouse, teclado e leitor de tela.
5. Feedback de carregamento, sucesso e erro deve ser visível e acessível.
6. Mudanças devem preservar os tokens semânticos, o tema dark/light e a linguagem visual do Reddit.
7. Preferir componentes Qt nativos e layouts responsivos a medições manuais.

## Escopo

### Incluído

- Feed autenticado e comunidades seguidas.
- Navegação entre Home, Popular, comunidades e posts salvos.
- Leitura de cards, mídia, detalhes e comentários.
- Votação, salvar, compartilhar, responder e criar posts.
- Pesquisa, tradução e reprodução de mídia como recursos secundários.
- Acessibilidade de controles, foco e estados assíncronos.
- Layout em 1440×900, 1024×768 e 920×620.

### Fora do escopo inicial

- Redesign visual completo.
- Mudança de identidade ou substituição dos tokens atuais.
- Implementação de novas APIs do Reddit.
- Reescrita da arquitetura de rede.
- Reprodução perfeita de cada detalhe interno do Reddit Web.

## Fases de execução

### Fase 0 — Baseline visual e funcional

**Objetivo:** criar uma referência comparável antes de cada mudança.

**Entregas**

- Capturas do app em 1440×900, 1024×768 e 920×620.
- Capturas dos estados:
  - Home autenticado.
  - Comunidade.
  - Busca.
  - Post salvo.
  - Detalhe de post.
  - Comentários expandidos e recolhidos.
  - Feed vazio.
  - Erro de rede.
  - Tema claro e escuro.
- Registro da posição inicial do foco e da posição do scroll.

**Critérios de aceite**

- Cada captura identifica resolução, tema e estado.
- A posição do feed e a visibilidade dos rails podem ser comparadas entre versões.
- Nenhum teste visual depende de dados externos instáveis sem fixture local.

### Fase 1 — Hierarquia e redução de ruído

**Arquivos principais**

- `main.py`
- `ui_theme.py`

**Alterações**

- Manter Jogos recolhido por padrão.
- Manter promoções e recursos secundários fora do caminho principal de leitura.
- Reduzir ações globais visíveis no header em larguras menores.
- Agrupar tradução, Ads e Chat em menu secundário quando necessário.
- Manter no rail direito apenas:
  - Posts recentes.
  - Contexto da comunidade atual.
  - Regras, quando disponíveis.
- Remover repetição de informação entre banner, feed e rail.
- Reduzir a quantidade de opções de ordenação visíveis simultaneamente sem remover funcionalidade.
- Preservar o banner apenas para comunidades reais; não renderizar contexto de comunidade em Home, busca e salvos.

**Critérios de aceite**

- Em 1440 px, o primeiro foco visual é o feed.
- Em 1024 px, o usuário consegue ler e abrir posts sem depender do rail direito.
- Home não apresenta um subreddit fictício como contexto.
- Nenhum badge, contador ou estado simulado aparece como informação remota.

### Fase 2 — Acessibilidade operacional

**Arquivos principais**

- `main.py`
- `widgets.py`
- `post_dialog.py`
- `login_dialog.py`
- `ui_theme.py`

**Alterações**

- Adicionar `setAccessibleName` e `setAccessibleDescription` aos controles restantes.
- Substituir `QLabel`/`QFrame` clicáveis por `QPushButton` ou controles focáveis quando representarem ações.
- Garantir foco visível em todos os controles interativos.
- Definir ordem de tabulação:
  1. Header.
  2. Navegação.
  3. Busca e filtros.
  4. Feed.
  5. Rail direito.
  6. Detalhes e comentários.
- Anunciar mudanças de estado em componente acessível equivalente a `aria-live`:
  - Carregamento.
  - Resultado da busca.
  - Voto.
  - Salvar.
  - Tradução.
  - Publicação.
  - Erros.
- Comunicar expandido/recolhido em seções, regras e comentários.
- Garantir que o foco vá para o primeiro campo inválido em formulários.
- Manter labels persistentes em campos de criação de post, login, busca e comunidades.

**Critérios de aceite**

- Todo botão iconográfico tem nome acessível.
- Todo campo tem label ou nome acessível independente do placeholder.
- A jornada principal pode ser concluída apenas com teclado.
- Leitor de tela consegue identificar ação, estado e resultado.
- Nenhuma ação depende somente de cor, símbolo ou hover.

### Fase 3 — Estados honestos e recuperação

**Arquivos principais**

- `main.py`
- `widgets.py`
- `post_dialog.py`
- `reddit_api.py`

**Alterações**

- Separar explicitamente estados:
  - Local.
  - Remoto confirmado.
  - Remoto pendente.
  - Indisponível.
- Voto, salvar, resposta e publicação devem exibir:
  - Estado de envio.
  - Sucesso confirmado.
  - Erro acionável.
  - Opção de tentar novamente.
- Ações destrutivas ou irreversíveis devem ter confirmação ou undo.
- “Limpar posts recentes” deve informar se remove apenas dados locais.
- Erros devem explicar o próximo passo, por exemplo:
  - “Não foi possível carregar o feed. Tentar novamente.”
  - “Você precisa entrar para votar.”
- Preservar o contexto após falhas e novas tentativas.

**Critérios de aceite**

- Nenhum feedback local afirma que uma ação remota foi concluída sem confirmação.
- Toda falha possui recuperação visível.
- O botão de envio fica desabilitado apenas durante a requisição.
- O usuário não perde o texto digitado ao ocorrer erro de rede.

### Fase 4 — Responsividade estrutural

**Arquivos principais**

- `main.py`
- `ui_theme.py`

**Breakpoints**

| Largura | Composição esperada |
|---:|---|
| 1440 px ou mais | Sidebar esquerda, feed central e rail direito |
| 1180–1439 px | Sidebar esquerda compacta, feed central e rail direito reduzido |
| 1000–1179 px | Sidebar compacta e feed central; rail direito colapsável |
| 920–999 px | Sidebar compacta, feed central prioritário, ações globais agrupadas |
| Abaixo de 920 px | Tratar como largura mínima suportada ou apresentar orientação clara |

**Alterações**

- Agrupar controles do header antes de ocultá-los.
- Oferecer painel colapsável para contexto da comunidade quando o rail desaparecer.
- Evitar que filtros e ações quebrem em uma única linha.
- Preservar largura legível dos títulos e do corpo dos posts.
- Garantir que textos longos, nomes de comunidades e títulos não causem overflow.
- Testar escalonamento de fonte e conteúdo traduzido.

**Critérios de aceite**

- Não há scroll horizontal involuntário.
- O título e as ações principais de um post continuam acessíveis em 920 px.
- O rail direito não é necessário para completar a jornada principal.
- A ordem visual e a ordem de foco permanecem equivalentes.

### Fase 5 — Jornada de post e comentários

**Arquivos principais**

- `widgets.py`
- `main.py`

**Alterações**

- Tornar o card inteiro e seus pontos de entrada semanticamente previsíveis.
- Destacar uma ação primária: abrir/ler o post.
- Manter voto, comentários, salvar e compartilhar como ações secundárias consistentes.
- Preservar posição do feed ao voltar do detalhe.
- Melhorar contexto de comentários profundos:
  - Autor.
  - Profundidade.
  - Estado recolhido.
  - Quantidade de respostas ocultas.
- Garantir que recolher/expandir funcione por teclado.
- Exibir estados vazios e erros de comentários sem quebrar a hierarquia.

**Critérios de aceite**

- Abrir um post é possível pelo teclado e pelo mouse.
- Voltar retorna ao mesmo feed e posição aproximada.
- Comentários recolhidos informam quantas respostas estão ocultas.
- Todas as ações de comentário têm feedback de sucesso ou erro.

### Fase 6 — Criação de posts e formulários

**Arquivos principais**

- `post_dialog.py`
- `login_dialog.py`

**Alterações**

- Adicionar labels persistentes aos campos.
- Organizar o diálogo em etapas visuais:
  1. Tipo de post.
  2. Conteúdo.
  3. Opções.
  4. Publicação.
- Mostrar apenas opções relevantes ao tipo de post escolhido.
- Validar inline título, corpo, URL, enquete e tags.
- Focar o primeiro erro.
- Manter preview como ação secundária.
- Evitar que placeholder seja a única explicação do campo.

**Critérios de aceite**

- Usuário consegue criar post sem depender de conhecimento prévio de Markdown.
- Erros aparecem junto ao campo e explicam como corrigir.
- Trocar o tipo de post não apaga conteúdo sem aviso.
- Estado de envio não permite publicação duplicada.

### Fase 7 — Refinamento visual

**Arquivos principais**

- `ui_theme.py`
- `main.py`
- `widgets.py`

**Alterações**

- Ajustar espaçamento e ritmo após a hierarquia estar correta.
- Padronizar labels de ordenação e ações.
- Reduzir símbolos ambíguos em ações importantes.
- Harmonizar alturas de botões, pills e áreas de foco.
- Verificar contraste em dark/light com conteúdo real.
- Respeitar preferência de redução de movimento do sistema Linux nas microanimações.

**Critérios de aceite**

- O sistema visual permanece reconhecível como Reddit.
- A cor laranja continua sendo acento, não ruído.
- Estados hover, pressed, focused, disabled, loading, empty e error são distinguíveis.
- Nenhuma animação é necessária para compreender ou concluir uma tarefa.

## Matriz de testes

### Testes automatizados

- `python3 tests/test_accessibility.py`
- `python3 -m compileall -q .`
- `git diff --check`
- Detector Impeccable:

```bash
node /home/janlucas/.local/share/com.github.githubapp/app-skills/impeccable/scripts/detect.mjs \
  --json main.py widgets.py ui_theme.py post_dialog.py login_dialog.py
```

### Testes funcionais

- Login e logout.
- Feed Home autenticado.
- Popular e comunidades.
- Pesquisa com resultado, sem resultado e erro.
- Abrir post e voltar.
- Expandir/recolher comentários.
- Votar com e sem login.
- Salvar e remover post salvo.
- Responder comentário.
- Criar post de texto, link, mídia e enquete.
- Traduzir feed, post e descrição.
- Abrir e fechar mídia.
- Tema claro/escuro.
- Feed vazio e falha de rede.

### Testes de teclado

- `Tab` e `Shift+Tab` atravessam todos os controles.
- `Enter` e `Space` ativam botões.
- `Escape` fecha detalhes/modais e retorna ao feed quando apropriado.
- `Alt+Left` retorna sem perder contexto.
- `Ctrl+F` e `Ctrl+K` focam busca.
- `Ctrl+N` abre criação de post.
- `J/K` navegam no feed sem interferir em campos de texto.

### Testes visuais

Executar para cada resolução e tema:

| Estado | 1440×900 | 1024×768 | 920×620 |
|---|:---:|:---:|:---:|
| Home autenticado | [ ] | [ ] | [ ] |
| Comunidade | [ ] | [ ] | [ ] |
| Busca | [ ] | [ ] | [ ] |
| Detalhe do post | [ ] | [ ] | [ ] |
| Comentários profundos | [ ] | [ ] | [ ] |
| Feed vazio | [ ] | [ ] | [ ] |
| Erro de rede | [ ] | [ ] | [ ] |
| Tema claro | [ ] | [ ] | [ ] |
| Tema escuro | [ ] | [ ] | [ ] |

## Ordem recomendada de commits

1. `refactor(ui): establish visual and functional baselines`
2. `fix(ui): reduce feed competition and secondary navigation noise`
3. `fix(a11y): complete names, focus order, and state announcements`
4. `fix(ui): make async and mutation states honest and recoverable`
5. `fix(ui): improve responsive feed and rail behavior`
6. `fix(comments): preserve context and improve keyboard interaction`
7. `fix(forms): improve post creation labels and inline validation`
8. `polish(ui): align spacing, copy, motion, and interaction states`

## Definition of Done

- A usuário autenticado consegue completar a jornada principal sem depender do rail direito.
- O feed é visualmente dominante em todas as larguras suportadas.
- Todos os controles importantes são operáveis por mouse e teclado.
- Estados assíncronos e remotos são honestos, recuperáveis e acessíveis.
- Home, comunidades, busca, salvos, detalhe e comentários têm contexto inequívoco.
- Dark/light permanecem contrastados e consistentes.
- Não há overflow horizontal ou perda de foco nas larguras suportadas.
- Testes automatizados, funcionais, de teclado e visuais estão registrados.
- Detector Impeccable não possui achados sem justificativa.

## Risco e mitigação

| Risco | Mitigação |
|---|---|
| Alterar a fidelidade ao Reddit Web | Comparar capturas antes/depois e preservar tokens existentes |
| Esconder recursos avançados | Usar menus e seções recolhíveis, não remover funcionalidades |
| Quebrar atalhos existentes | Cobrir atalhos com testes de teclado |
| Estados remotos inconsistentes | Separar estado pendente, confirmado, local e erro |
| Layout quebrar em largura intermediária | Testar 1440, 1024 e 920 px após cada fase estrutural |
| Acessibilidade incompleta | Validar nomes, foco, ordem e anúncios em cada jornada |

---

# Especificação Visual Detalhada

Esta seção transforma o plano em uma especificação de reconstrução visual e comportamental do Reddit Web para o cliente PySide6.

## 1. Referência visual das imagens anexadas

As imagens anexadas definem a seguinte composição de referência:

- Canvas geral escuro, quase preto, sem gradiente decorativo.
- Header horizontal fixo com altura aproximada de 56–64 px.
- Logo `reddit` alinhado à esquerda.
- Busca central em uma cápsula larga.
- Grupo de ações globais alinhado à direita.
- Sidebar esquerda fixa com navegação principal e seções recolhíveis.
- Separador vertical entre sidebar e conteúdo.
- Feed central estreito, com leitura vertical contínua.
- Rail direito com card de posts recentes e contexto.
- Cards de posts sem excesso de contorno; o conteúdo e o espaçamento criam a separação.
- Ação principal do post concentrada em uma linha inferior de pills.
- Tipografia branca/cinza sobre fundo escuro, com laranja apenas para marca, foco e estados de voto.

As imagens são referência de proporção, hierarquia e comportamento. Conteúdo real, contadores e estado de login devem vir da aplicação, nunca ser copiados como valores fixos.

## 2. Grade visual global

### 2.1 Janela

| Elemento | Regra |
|---|---|
| Largura mínima | 920 px, com aviso ou fallback abaixo desse limite |
| Altura mínima | 620 px |
| Header | 56–64 px, fixo no topo |
| Sidebar esquerda | 240–272 px aberta; 64–72 px recolhida |
| Feed | 620–760 px em desktop largo; nunca menor que o necessário para títulos e ações |
| Rail direito | 300–340 px em desktop largo |
| Gap entre colunas | 16–24 px |
| Margem do feed | 16–24 px vertical; alinhamento central |
| Base de espaçamento | 4 px |
| Raios | 6 px para cards, 18–24 px para pills, 50% para avatars |

### 2.2 Camadas

1. **Canvas:** fundo da janela.
2. **Chrome:** header, sidebar e divisores.
3. **Conteúdo:** feed e rail.
4. **Superfícies interativas:** cards, inputs, pills e menus.
5. **Overlays:** menus, diálogos, toasts e visualizador de mídia.

Nenhuma camada deve usar sombra forte para compensar hierarquia insuficiente. A hierarquia deve vir primeiro de posição, espaçamento, contraste e peso tipográfico.

## 3. Header completo

### 3.1 Botão Menu lateral

**Aparência**

- Ícone: `☰` ou ícone vetorial de menu.
- Área mínima: 40×40 px.
- Alinhado ao início do header.
- Hover: superfície interativa.
- Focus: borda/anel laranja.

**Comportamento**

- Clique alterna sidebar aberta/recolhida.
- Atalho equivalente: nenhum obrigatório; `Tab`, `Enter` e `Space` devem funcionar.
- Em largura abaixo de 1000 px, a sidebar recolhe automaticamente uma vez, sem impedir reabertura manual.

**Acessibilidade**

- Nome: “Alternar barra lateral”.
- Descrição informa o estado atual: “Barra lateral aberta” ou “Barra lateral recolhida”.

### 3.2 Logo Reddit

**Aparência**

- Texto `reddit` ou marca vetorial.
- Peso alto, sem slogan.
- Cor neutra no repouso; laranja no hover opcional.

**Comportamento**

- Clique retorna ao feed Popular ou Home conforme sessão.
- Não deve abrir nova janela.

**Acessibilidade**

- Nome: “Ir para o feed inicial”.
- Se for botão, manter foco visível.

### 3.3 Busca global

**Aparência**

- Cápsula central, aproximadamente 420–620 px no desktop.
- Ícone Snoo ou lupa à esquerda.
- Placeholder: `Buscar no Reddit…`.
- Texto alinhado verticalmente ao centro.
- Sem botão separado para submeter.

**Comportamento**

- `Enter`: executa busca.
- `Ctrl+F` e `Ctrl+K`: focam o campo.
- `Escape`: limpa foco ou fecha resultados, sem apagar o texto.
- Busca deve suportar posts, comunidades e resultados vazios.

**Estados**

- Repouso.
- Hover.
- Foco.
- Buscando.
- Resultado.
- Sem resultado.
- Erro.

**Acessibilidade**

- Nome: “Buscar no Reddit”.
- Não usar placeholder como única label.
- Mensagem de resultado deve ser anunciada.

### 3.4 Menu de tradução

**Ícone**

- Tradução/idioma.
- Tooltip: “Tradução e idioma”.

**Menu**

1. Traduzir página atual.
2. Auto-traduzir ao abrir.
3. Idioma atual.
4. Lista de idiomas suportados.

**Regras**

- Idioma selecionado tem marca visual e textual.
- Estado `Traduzindo…` não deve parecer ação concluída.
- Falha mantém conteúdo original e oferece tentar novamente.

### 3.5 Menu Ads

**Ícone**

- Ícone de anúncio/documento.
- Tooltip: “Abrir Reddit Ads”.

**Comportamento**

- Abre URL externa em navegador padrão.
- Deve ser secundário e agrupado em larguras menores.
- Não deve competir visualmente com Criar.

### 3.6 Chat

**Ícone**

- Balão de conversa.
- Tooltip: “Abrir Reddit Chat”.

**Comportamento**

- Abre o Chat externo.
- Se não houver suporte local, o menu deve dizer “Abrir no Reddit Web”, evitando sugerir integração nativa inexistente.

### 3.7 Botão Criar

**Aparência**

- Ícone `+` ou documento com `+`.
- Texto `Criar`.
- Mais proeminente que Ads, Chat e tradução.

**Comportamento**

- Abre diálogo de criação.
- Atalho: `Ctrl+N`.
- Não deve abrir se o usuário não estiver autenticado; nesse caso, abrir login e explicar o motivo.

**Acessibilidade**

- Nome: “Criar publicação”.
- Descrição: “Abrir formulário para criar uma publicação no Reddit”.

### 3.8 Notificações

**Aparência**

- Sino sem badge quando não houver contagem remota confirmada.
- Badge só aparece se vier de estado real.

**Menu**

1. Posts salvos, com contagem local explícita.
2. Abrir Inbox/notificações no Reddit Web.

**Proibição**

- Não exibir contador fictício.
- Não oferecer “Marcar como lidas” se a ação apenas esconder o badge localmente.

### 3.9 Área de usuário

**Estado deslogado**

- Botão `Entrar`.
- Avatar substituto discreto.

**Estado logado**

- Avatar/Snoovatar.
- Indicador de presença somente se representar estado real.
- Menu de perfil:
  1. Perfil.
  2. Configurações.
  3. Tema.
  4. Idioma.
  5. Sair.

## 4. Sidebar esquerda

### 4.1 Estrutura

Ordem visual obrigatória:

1. Navegação principal.
2. Jogos no Reddit.
3. Feeds personalizados.
4. Recentes.
5. Comunidades.

Cada grupo deve ter:

- Título de seção.
- Botão de expandir/recolher.
- Estado persistente durante a sessão.
- Indicador textual de estado para acessibilidade.

### 4.2 Navegação principal

#### Feed principal

- Ícone: casa.
- Label: `Feed principal`.
- Estado ativo: fundo destacado, ícone e texto com contraste maior.
- Clique: Home autenticado; Popular se deslogado.

#### Popular

- Ícone: tendência/raio.
- Label: `Popular`.
- Clique: feed Popular.

#### Notícias

- Ícone: jornal.
- Label: `Notícias`.
- Clique: feed de notícias ou rota externa claramente identificada.

#### Explorar

- Ícone: rede/exploração.
- Label: `Explorar`.
- Clique: comunidades e descoberta.

#### Criar comunidade

- Ícone: `+`.
- Label: `Criar comunidade`.
- Se ainda não implementado localmente, abrir explicação e oferecer Reddit Web.

### 4.3 Seção Jogos no Reddit

**Objetivo:** descoberta secundária, não ocupar o caminho de leitura.

**Estado padrão:** recolhida.

**Cabeçalho**

- Texto: `JOGOS NO REDDIT`.
- Ícone: chevron para cima/baixo.
- Clique: alterna conteúdo.

**Card destacado**

- Avatar da comunidade.
- Nome do jogo.
- Subtítulo curto.
- Métrica somente se real; caso seja editorial, rotular como recomendação.
- Clique abre comunidade.

**Itens de lista**

- Ícone pequeno.
- Nome truncado.
- Subreddit ou categoria.
- Área de clique de pelo menos 40 px.

### 4.4 Feeds personalizados

**Cabeçalho**

- Texto: `FEEDS PERSONALIZADOS`.
- Chevron.

**Ação Criar feed**

- Ícone `+`.
- Abre input ou fluxo de criação.
- Validação inline.

**Posts salvos**

- Ícone bookmark.
- Abre lista local.
- Label deve indicar “salvos neste computador” quando não sincronizado.

### 4.5 Recentes

- Lista de comunidades visitadas.
- Avatar circular.
- Nome `r/comunidade`.
- Tempo opcional.
- Empty state: “As comunidades visitadas aparecerão aqui.”

### 4.6 Comunidades

**Campo Adicionar**

- Placeholder: `Adicionar r/…`.
- Label persistente: “Adicionar comunidade”.
- Botão `+` separado, com nome acessível.
- `Enter` submete.
- Erro fica abaixo do campo e não em toast isolado.

**Item de comunidade**

- Avatar.
- Nome.
- Estado ativo.
- Botão de remoção separado, com confirmação/undo.

## 5. Feed central

### 5.1 Tabs

#### Para você

- Feed recomendado/personalizado.
- Estado ativo com underline e contraste.
- Nome acessível: “Feed Para você”.

#### Seguindo

- Feed das comunidades seguidas.
- Se não houver comunidades, mostrar onboarding curto e ação para explorar.

### 5.2 Ordenação

Opções:

- `Em alta`.
- `Novos`.
- `Mais votados`.
- `Subindo`.

Regras:

- Apenas uma opção ativa.
- O estado ativo deve ser percebido por texto, cor e forma.
- Em `Mais votados`, exibir filtro de período:
  - Hoje.
  - Esta semana.
  - Este mês.
  - Este ano.
  - Desde sempre.

Em larguras menores, agrupar ordenação em um menu `Ordenar por`.

### 5.3 Alternância Cards/Lista

#### Cards

- Mostra imagem, texto e ações completas.
- Estado recomendado para leitura visual.

#### Lista

- Reduz mídia e altura.
- Mantém título, comunidade, autor, votos, comentários e ações essenciais.

Cada botão deve indicar o modo atual e o modo que será ativado.

### 5.4 Card de post

#### Cabeçalho

- Avatar da comunidade.
- `r/subreddit`.
- Autor e tempo.
- Botão `Unir-se` quando aplicável.
- Botão `…` para mais opções.

#### Menu de mais opções

Itens possíveis:

1. Abrir post.
2. Abrir no Reddit Web.
3. Salvar/remover dos salvos.
4. Compartilhar link.
5. Ocultar post.
6. Reportar, se suportado.

Itens indisponíveis devem ser ocultados ou desabilitados com explicação.

#### Badges

- NSFW.
- Spoiler.
- OC.
- Fixado.
- Vídeo.
- Flair.

Badges são informativos, não devem parecer botões se não forem interativos.

#### Título

- 16–20 px conforme contexto.
- Até 2–3 linhas no feed.
- Clique abre detalhe.
- Deve ser focável ou acompanhado por botão semanticamente equivalente.

#### Corpo

- Preview limitado.
- Texto longo deve usar line clamp/“ver mais”.
- Conteúdo gerado pelo usuário não pode quebrar layout.

#### Mídia

- Dimensões reservadas antes do carregamento.
- Imagem com proporção preservada.
- Vídeo com poster, play e controle de áudio.
- Galeria exibe quantidade e navegação.
- Mídia decorativa recebe descrição vazia; mídia informativa recebe descrição contextual.

#### Linha de ações

##### Voto

- Uma pill unificada com:
  - Upvote.
  - Score.
  - Downvote.
- Cada ícone é botão independente.
- Estado votado usa cor sem depender apenas de cor.
- Tooltip explica ação.

##### Comentários

- Ícone balão.
- Contagem real.
- Clique abre detalhe.

##### Tradução

- Ícone tradução.
- Estado:
  - `Traduzir`.
  - `Traduzindo…`.
  - `Traduzido`.
  - `Falha — tentar novamente`.

##### Compartilhar

- Ícone compartilhar.
- Copia link e mostra confirmação.
- Deve permitir abrir menu de compartilhamento quando disponível.

##### Salvar

- Ícone bookmark.
- Estado `Salvar`/`Salvo`.
- Se local, informar que o dado está salvo neste computador.

## 6. Rail direito

### 6.1 Posts recentes

**Cabeçalho**

- Texto `POSTS RECENTES`.
- Ação `Limpar`.

**Item**

- Avatar.
- Comunidade.
- Tempo.
- Título com até 2–3 linhas.
- Votos e comentários quando disponíveis.

**Limpar**

- Deve limpar somente histórico local, com texto explícito.
- Oferecer undo por alguns segundos ou confirmação se a remoção for permanente.

### 6.2 Sobre a comunidade

- Nome da comunidade/feed.
- Descrição.
- Membros.
- Online, somente se real.
- Botão traduzir descrição.
- Regras em acordeão.

No Home, substituir “r/popular” por contexto real:

- `Seu Feed`.
- “Postagens das comunidades em que você está inscrito”.

### 6.3 Adaptação

Quando o rail desaparecer:

- Mostrar botão `Abrir contexto` próximo ao título do feed.
- Ou transformar o contexto em painel colapsável abaixo da barra de filtros.
- Nunca remover informação essencial sem alternativa.

## 7. Detalhe do post

### Header

- Botão `Voltar ao feed`.
- Botão traduzir post e comentários.
- Botão abrir no Reddit Web.

### Conteúdo

- Comunidade, autor, tempo.
- Título completo.
- Corpo completo.
- Mídia em tamanho confortável.
- Galeria com anterior/próxima.
- Estado de vídeo e áudio.

### Comentários

- Título com contagem.
- Campo de comentário.
- Botão `Publicar comentário`.
- Ordenação de comentários.
- Árvore recolhível.

## 8. Comentários

### Linha de comentário

- Avatar.
- Autor.
- Tempo.
- Corpo.
- Score.
- Votar.
- Responder.
- Traduzir.
- Recolher/expandir.

### Regras de profundidade

- Limitar recuo visual.
- Exibir texto “N respostas ocultas”.
- Usar linha guia com contraste suficiente.
- Nunca depender somente de cor para indicar profundidade.

### Resposta

- Campo contextual abaixo do comentário.
- Placeholder: `Responder a u/autor em Markdown…`.
- Botões `Cancelar` e `Publicar resposta`.
- Erro fica no campo.

## 9. Diálogo de criação de post

### Tabs de tipo

1. `Post (Markdown)`.
2. `Imagens e vídeo`.
3. `Link/URL`.
4. `Enquete`.

Cada tab deve:

- Ter ícone e texto.
- Ser focável.
- Indicar seleção.
- Preservar dados ao trocar, ou pedir confirmação antes de descartar.

### Campos

#### Título

- Label: `Título`.
- Obrigatório.
- Contador opcional.
- Erro: “Adicione um título para publicar.”

#### Corpo

- Label: `Texto da publicação`.
- Markdown suportado.
- Preview separado.

#### Mídia

- Label: `Imagem ou vídeo`.
- Seletor de arquivo, URL ou arrastar/soltar quando suportado.
- Mostrar preview, tamanho e erro.

#### Link

- Label: `URL`.
- Tipo URL.
- Validação de esquema.

#### Enquete

- Pergunta.
- Opções.
- Duração.
- Remover opção.
- Adicionar opção.

### Tags

- OC.
- Spoiler.
- NSFW.

Cada tag:

- Tem estado ligado/desligado.
- Não depende apenas de cor.
- Explica impacto no conteúdo.

### Ações finais

- `Cancelar`: fecha sem publicar; se houver conteúdo, confirmar descarte.
- `Pré-visualizar`: mostra resultado sem enviar.
- `Publicar no Reddit`: ação primária.

## 10. Menus e estados globais

### Toast

Usar somente para confirmação breve, não para erros que exigem decisão.

Tipos:

- Info.
- Sucesso.
- Aviso.
- Erro.

Cada toast deve:

- Ter texto específico.
- Explicar próximo passo quando houver erro.
- Ser anunciado acessivelmente.
- Não cobrir o botão que acabou de ser usado.
- Desaparecer sem interromper foco.

### Menu contextual

- Abre próximo ao controle de origem.
- Fecha com Escape.
- Mantém foco no item selecionado.
- Não pode sair da janela em bordas.

### Modal

- Usar apenas para login, criação, mídia ou ação que exige foco protegido.
- Não usar modal para filtros simples ou contexto de comunidade.
- Foco inicial deve ser justificado.
- Fechamento por Escape e botão visível.

## 11. Inventário de ícones

| Ícone | Uso | Regra |
|---|---|---|
| Casa | Feed principal | Deve ter label |
| Tendência/raio | Popular | Não usar apenas como significado |
| Jornal | Notícias | Acompanhar texto |
| Rede | Explorar | Acompanhar texto |
| `+` | Criar/adicionar | Nomear ação |
| Menu | Sidebar | Informar estado |
| Snoo/lupa | Busca | Não substituir label |
| Tradução | Idioma | Tooltip e nome acessível |
| Ads | Anúncio | Marcar como externo |
| Chat | Conversa | Marcar como externo quando aplicável |
| Sino | Notificações | Badge somente com estado real |
| Avatar | Perfil | Nomear usuário/ação |
| Chevron | Expandir/recolher | Anunciar estado |
| Bookmark | Salvar | Estado Salvo/Salvar |
| Balão | Comentários | Incluir contagem |
| Upvote/downvote | Votação | Nomear direção |
| Compartilhar | Compartilhar | Confirmar cópia/ação |
| Tradução em post | Traduzir | Expor estados |
| Play | Vídeo | Nomear ação |
| `…` | Mais opções | Nunca deixar sem label |
| X | Fechar/remover | Informar o que será fechado/removido |

## 12. Contratos de estado

Todo componente interativo deve implementar:

```text
idle
hover
focused
pressed
disabled
loading
success
error
empty
```

Para ações de rede, usar adicionalmente:

```text
local
pending
confirmed
rejected
retryable
```

O texto e o visual devem diferenciar `local` de `confirmed`.

## 13. Plano de execução atualizado

### Sprint 1 — Shell e header

- Recriar medidas do header.
- Reorganizar ações globais.
- Implementar busca, foco e menus.
- Validar screenshot desktop.

### Sprint 2 — Sidebar e navegação

- Recriar grupos e estados.
- Implementar recolhimento.
- Corrigir labels, chevrons e foco.
- Validar Home, Popular, Notícias e Explorar.

### Sprint 3 — Feed e card

- Recriar cabeçalho, título, mídia e linha de ações.
- Padronizar estados de voto, salvar e tradução.
- Validar conteúdo curto, longo, imagem, vídeo e galeria.

### Sprint 4 — Rail direito

- Recriar posts recentes.
- Recriar contexto de comunidade.
- Definir painel colapsável para telas intermediárias.

### Sprint 5 — Detalhe e comentários

- Implementar retorno com restauração de contexto.
- Recriar árvore de comentários.
- Testar recolhimento, resposta e tradução.

### Sprint 6 — Criação e autenticação

- Recriar diálogo de criação.
- Adicionar labels, validação e estados de envio.
- Validar login, logout e permissões.

### Sprint 7 — Responsividade e acessibilidade

- Testar cada breakpoint.
- Completar nomes acessíveis.
- Validar foco, teclado e anúncios.

### Sprint 8 — Comparação visual final

- Capturar todos os estados.
- Comparar contra as imagens anexadas e referências oficiais.
- Corrigir somente diferenças de alto impacto.
- Executar a suíte completa e registrar os resultados.

## 14. Checklist de cada componente

Antes de considerar qualquer componente pronto, responder:

- Qual é a função primária?
- Qual é o texto visível?
- Qual é o ícone?
- Qual é o tooltip?
- Qual é o nome acessível?
- Como funciona com teclado?
- Qual é o foco visual?
- Quais são os estados idle, hover, focus, pressed, disabled, loading, success, error e empty?
- Qual é o comportamento em 1440, 1024 e 920 px?
- O componente usa estado real, local ou pendente?
- O que acontece se a rede falhar?
- O que acontece se o texto for muito longo?
- O que acontece se o usuário estiver deslogado?
- O que acontece ao pressionar Escape?
- O foco é preservado depois da ação?
- O componente ainda faz sentido no tema claro?

## 15. Critério final de paridade

A reconstrução será considerada aprovada quando:

1. A silhueta da janela for reconhecível como Reddit Web sem olhar detalhes.
2. A busca, o feed, a sidebar e o rail ocuparem posições equivalentes às imagens de referência.
3. O feed continuar sendo o caminho visual dominante.
4. Todos os botões e ícones possuírem ação e nome claros.
5. Menus e diálogos abrirem, fecharem e devolverem foco de modo previsível.
6. O usuário conseguir ler, votar, comentar, salvar, pesquisar e criar post sem depender de um único modo de entrada.
7. Estados vazios, carregamento e erro parecerem parte do produto, não exceções improvisadas.
8. A composição continuar utilizável em 1440×900, 1024×768 e 920×620.
9. A implementação não apresentar dados fictícios como se fossem informações do Reddit.
10. A comparação visual final não revelar diferenças estruturais de alto impacto.

