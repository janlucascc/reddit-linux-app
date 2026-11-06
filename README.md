# Reddit Linux Desktop 🐧

<div align="center">

<img src="reddit-linux.svg" alt="Reddit Linux Logo" width="120" height="120" />

### *Cliente nativo, ultrarrápido e moderno do Reddit para Linux com tradução multilíngue integrada.*

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Qt](https://img.shields.io/badge/GUI-PySide6%20%2F%20Qt6-41CD52?logo=qt&logoColor=white)](https://wiki.qt.io/Qt_for_Python)
[![Linux](https://img.shields.io/badge/Platform-Linux-FCC624?logo=linux&logoColor=black)](https://www.kernel.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![No API Key](https://img.shields.io/badge/Setup-Pronto%20para%20Uso%20(Sem%20API%20Key)-brightgreen.svg)](#como-funciona)

[Recursos](#-recursos-principais) • [Instalação](#-instalação-e-execução) • [Tradução](#-sistema-de-tradução-inteligente) • [Atalhos](#-atalhos-de-teclado) • [Licença](#-licença)

</div>

---

## 🌟 Visão Geral

O **Reddit Linux Desktop** é uma aplicação desktop nativa construída em **Python** e **PySide6 (Qt6)** projetada para oferecer a melhor experiência do Reddit no ecossistema Linux:

* 🚫 **Zero Electron / Zero WebViews no Feed**: Consumo mínimo de memória RAM e inicialização instantânea.
* ⚡ **Pronto para Usar**: Funciona diretamente, sem exigir chaves de API pagas ou cadastros de desenvolvedor.
* 🌐 **Login no seu Navegador Padrão**: Conecte com segurança no seu navegador favorito (Brave, Chrome ou Firefox) com 1 clique, aproveitando sua sessão já aberta e gerenciador de senhas.
* 🎨 **Visual Shreddit Fiel**: Inspirado na interface moderna do Reddit Web, com suporte completo a **Modo Escuro nativo** e **Modo Claro**.
* 🌐 **Tradução Multilíngue**: Traduza postagens individuais, comentários específicos ou a página inteira em lote com 1 clique (para Português, Inglês, Espanhol, Francês, Alemão, Italiano ou Japonês).

---

## 🚀 Recursos Principais

### 📰 Navegação e Feeds
* **Autenticação Simples**: Clique em `👤 Entrar` para se autenticar pela tela oficial do Reddit. Ao entrar, o seu feed personalizado (`🏠 Início`) e karma ficam imediatamente disponíveis.
* **Votação Interativa**: Vote com `▲` (Upvote) ou `▼` (Downvote) diretamente nos cards do feed com atualização visual de pontuação em tempo real.
* **Feeds Principais**: Acesso rápido a `r/popular`, `r/all` e comunidades locais.
* **Ordenação Completa**: Alterne entre **Em Alta (Hot)**, **Novos (New)**, **Topo (Top)** (com filtros de período: dia, semana, mês, ano ou sempre) e **Subindo (Rising)**.
* **Busca Global**: Pesquisa inteligente de postagens e comunidades com suporte a paginação contínua.
* **Barra Lateral Customizável**: Adicione e remova suas comunidades favoritas (`r/linux`, `r/brasil`, etc.) com 1 clique.
* **Posts Salvos**: Salve postagens favoritas localmente para leitura offline.

### 🖼️ Mídia e Galerias
* **Galerias Nativas**: Navegue entre múltiplas fotos com controles anteriores/próximos.
* **Visualizador em Alta Resolução**: Modal interativo com suporte a zoom (`🔍 −`, `🔍 +`, `↔ Ajustar Largura`, `100%`).
* **Tratamento de Infográficos**: Ajuste automático para imagens verticais longas e tirinhas sem distorção.
* **Suporte a Vídeos (`v.redd.it`)**: Abertura e reprodução direta no player do sistema (`mpv`, `vlc`, etc.) ou navegador.

### 🌐 Sistema de Tradução Inteligente
* **Tradução Individual**: Botão `🌐 Traduzir` / `🔄 Ver Original` em cada postagem e comentário.
* **Tradução da Página em Lote (`Ctrl+T`)**: Traduz todos os títulos e prévias visíveis simultaneamente.
* **Modo Auto-Traduzir**: Opção `Auto-Traduzir: ON/OFF` para traduzir feeds automaticamente ao navegar.
* **Cache LRU em Memória**: Reaproveita traduções já realizadas sem realizar requisições redundantes de rede.

---

## ⌨️ Atalhos de Teclado

| Atalho | Ação |
| :--- | :--- |
| `Ctrl + T` | Traduzir ou restaurar a página atual inteira em lote |
| `Ctrl + F` | Focar na barra de busca global |
| `F5` | Atualizar o feed ou pesquisa ativa |
| `Esc` ou `Alt + ←` | Voltar da postagem para a lista de feeds |

---

## 📦 Instalação e Execução

### 1. Pré-requisitos
Certifique-se de ter o Python 3.10+ e o gerenciador de pacotes da sua distribuição Linux:

```bash
# Fedora / RHEL
sudo dnf install python3 python3-pip

# Ubuntu / Debian
sudo apt install python3 python3-pip

# Arch Linux
sudo pacman -S python python-pip
```

### 2. Clonar o Repositório
```bash
git clone https://github.com/janlucascc/reddit-linux-app.git
cd reddit-linux-app
```

### 3. Instalar Dependências
```bash
pip install -r requirements.txt
```

### 4. Executar a Aplicação
```bash
python3 main.py
```

### 5. (Opcional) Instalar no Menu de Aplicativos do Sistema
Para criar o lançador no menu do sistema e o comando `reddit-linux`:

```bash
mkdir -p ~/.local/share/applications ~/.local/bin
cp reddit-linux.desktop ~/.local/share/applications/
ln -sf $(pwd)/main.py ~/.local/bin/reddit-linux
chmod +x main.py
```

---

## 🛠️ Arquitetura do Projeto

```text
reddit-linux-app/
├── main.py               # Janela principal, ciclo de vida e gerenciamento multithread
├── browser_auth.py       # Servidor local e diálogo de login via navegador padrão
├── login_dialog.py       # Diálogo modal alternativo de login oficial via WebEngine
├── reddit_api.py         # Cliente de API e feeds com paginação e backoff exponencial
├── translator.py         # Serviço de tradução assíncrono com cache LRU
├── widgets.py            # Componentes gráficos de posts, comentários, avatares e diálogos
├── ui_theme.py           # Paletas nativas (QPalette) e estilos QSS escuro/claro
├── reddit-linux.svg      # Ícone vetorial da aplicação
├── reddit-linux.desktop  # Atalho para integração no desktop Linux
├── requirements.txt      # Dependências do projeto
└── LICENSE               # Licença de uso livre (MIT)
```

---

## 📄 Licença

Distribuído sob a licença **MIT**. Veja o arquivo [`LICENSE`](LICENSE) para mais detalhes.

---

<div align="center">
Desenvolvido com carinho para a comunidade Linux. 🐧
</div>
