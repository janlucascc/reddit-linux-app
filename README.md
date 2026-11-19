<div align="center">

<img src="reddit-linux.svg" alt="Reddit Linux Logo" width="96" height="96" />

# Reddit Linux Desktop

**Cliente desktop nativo, ultrarrápido e moderno do Reddit para Linux.**  
Construído com Python e PySide6 (Qt6), com foco em desempenho, privacidade e tradução integrada.

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Qt](https://img.shields.io/badge/GUI-PySide6%20%2F%20Qt6-41CD52?logo=qt&logoColor=white)](https://wiki.qt.io/Qt_for_Python)
[![Platform](https://img.shields.io/badge/Platform-Linux-FCC624?logo=linux&logoColor=black)](https://www.kernel.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Zero Config](https://img.shields.io/badge/Setup-Pronto%20para%20Uso-brightgreen.svg)](#-instalação-rápida)

</div>

---

## ⚡ Por que usar?

* 🚫 **Zero Electron**: Consumo mínimo de CPU e memória RAM com inicialização instantânea em C++/Qt6.
* 🔓 **Pronto para Usar**: Navegue sem precisar cadastrar chaves de API pagas ou tokens complexos.
* 🌐 **Tradução Multilíngue**: Tradução em 1 clique de postagens, comentários ou páginas inteiras com cache rápido.
* 🔒 **Login Seguro**: Conexão opcional e simplificada via navegador padrão (Brave, Chrome ou Firefox).
* 🎨 **Interface Moderna**: Visual limpo inspirado no Shreddit, suporte nativo a Tema Claro e Escuro (WCAG AAA) e ícones vetoriais nítidos em High-DPI.

---

## ✨ Recursos Principais

* **Feeds e Comunidades**: Navegue por `r/popular`, `r/all`, feed inicial e adicione suas comunidades favoritas.
* **Ordenação Completa**: Alterne entre *Em Alta*, *Novos*, *Topo* (com filtros temporais) e *Subindo*.
* **Visualização Flexível**: Escolha entre modo **Cards** detalhado e modo **Lista** compacto.
* **Mídia e Vídeos Nativos**: Reprodução fluida de vídeos (`v.redd.it`, HLS, MP4) com áudio sincronizado e galerias com zoom.
* **Árvore de Comentários Completa**: Carregamento rápido com guias visuais e colapso de subárvores.
* **Autoria e Interação**: Crie novas publicações em Markdown, comente e vote em tempo real.
* **Leitura Offline**: Salve postagens localmente para consultar quando quiser.

---

## ⌨️ Atalhos de Teclado

| Tecla | Ação |
| :--- | :--- |
| `Ctrl + K` / `Ctrl + F` | Focar na busca rápida de posts e subreddits |
| `Ctrl + T` | Traduzir ou restaurar a tela atual |
| `Ctrl + N` | Criar nova publicação |
| `J` / `K` | Rolar para o próximo post / post anterior |
| `R` / `F5` | Atualizar o feed ativo |
| `Esc` | Voltar da postagem para o feed / fechar modal |

---

## 🚀 Instalação Rápida

### 1. Dependências do Sistema

```bash
# Ubuntu / Debian
sudo apt install python3 python3-pip

# Fedora / RHEL
sudo dnf install python3 python3-pip

# Arch Linux
sudo pacman -S python python-pip
```

### 2. Clonar e Executar

```bash
git clone https://github.com/janlucascc/reddit-linux-app.git
cd reddit-linux-app
pip install -r requirements.txt
python3 main.py
```

### 3. Integração com o Desktop (Opcional)

Para adicionar o Reddit Linux ao menu de aplicativos do seu sistema:

```bash
mkdir -p ~/.local/share/applications ~/.local/bin
cp reddit-linux.desktop ~/.local/share/applications/
ln -sf $(pwd)/main.py ~/.local/bin/reddit-linux
chmod +x main.py
```

---

## 🛠️ Estrutura do Código

```text
reddit-linux-app/
├── main.py               # Janela principal, navegação e orquestração assíncrona
├── reddit_api.py         # Cliente HTTP do Reddit com paginação e fallbacks
├── widgets.py            # Componentes visuais (cards, comentários, vídeo, avatares)
├── ui_theme.py           # Sistema de temas centralizado (Tokens e QSS Dark/Light)
├── translator.py         # Serviço de tradução com cache LRU em memória
├── browser_auth.py       # Autenticação OAuth2 / navegador local
├── post_dialog.py        # Diálogo para criação e envio de posts
├── login_dialog.py       # Diálogo alternativo de login
└── tests/                # Testes automatizados de contraste e acessibilidade
```

---

## 📄 Licença

Distribuído sob a licença [MIT](LICENSE).
