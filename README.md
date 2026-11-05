# 📚 YomiCalibre — Lecteur & Mineur Japonais (Calibre + Anki Data Mining)

Un lecteur web d'EPUB japonais auto-hébergeable, pensé spécifiquement pour **remplacer Yomitan sur mobile et PC** en se branchant directement sur votre bibliothèque **Calibre** et votre écosystème **Anki**.

---

## ✨ Fonctionnalités clés

1. **Zéro élément flottant · Lecture 100 % épurée**
   * Les chapitres sont pré-découpés côté serveur : **rien ne s'affiche tant que vous ne cliquez pas sur un mot** (aucune bulle flottante qui cache le texte, aucun furigana parasite).
   * Au clic sur un mot, seul ce mot est souligné et ses informations apparaissent dans le **panneau fixe intégré en bas de l'écran** (qui fait partie du flux de la page et ne survole jamais votre texte).
2. **Découpeur de phrases et de mots entraîné (SudachiPy + Normaliseur EPUB)**
   * **Gestion des polices et balises EPUB :** Fusionne les changements de police au milieu d'une phrase (`<span class="gothic">`, `<b>`, `<em>`) et nettoie les annotations `<ruby><rt>` intégrées pour ne jamais couper un mot ou une phrase en deux.
   * **Gestion des espacements japonais :** Détecte les alinéas (`　`), les espaces plein-chasse après `！` / `？`, les dialogues imbriqués (`「...」と彼は言った。`) et corrige les espacements d'emphase (`ぜ っ た い に` → `絶対に`).
   * **Agrégation des conjugaisons :** Regroupe automatiquement les verbes/adjectifs conjugués avec leurs auxiliaires (ex. `踏み入れた` → forme de base `踏み入れる`).
3. **Dictionnaire JMdict Français + Anglais & Lectures Hiragana / Katakana**
   * Affiche immédiatement la lecture en **Hiragana** (`平仮名`) et en **Katakana** (`片仮名`), la nature grammaticale et les définitions en **Français (prioritaire)** et **Anglais (complément)**.
4. **Création automatique de carte Anki avec phrase de contexte et mot souligné**
   * Dès que vous cliquez sur un mot (avec le mode **⚡ Auto-Anki** activé), une carte est créée pour le deck **`Data Mining`** avec :
     * Le mot et ses lectures (Hiragana + Katakana),
     * Les définitions FR + EN,
     * **La phrase complète du livre avec le mot cliqué souligné (`<u>mot</u>`)**.
   * **Synchronisation transparente avec votre Anki Server :**
     * Quand vous lisez sur votre téléphone, les cartes sont enregistrées instantanément sur votre serveur.
     * Dès que vous ouvrez l'interface sur votre PC portable (avec Anki ouvert) ou cliquez sur *Synchroniser*, toutes les cartes en attente sont poussées automatiquement dans le deck `Data Mining` via **AnkiConnect**, puis synchronisées vers votre **Anki Sync Server** !
     * Vous pouvez aussi télécharger à tout moment un paquet **`.apkg`** en 1 clic sur mobile ou PC.

---

## 🚀 Déploiement avec Dokploy (Recommandé)

Le projet est préconfiguré pour **Dokploy** (avec `HEALTHCHECK` intégré sur `/api/health`, port `8085` et téléchargement automatique du dictionnaire complet JMdict FR/EN au premier lancement).

### Option A — Déployer en tant qu'**Application** dans Dokploy (via Git / Dockerfile)

1. Poussez ce dossier sur votre dépôt **GitHub / GitLab / Gitea**.
2. Dans **Dokploy**, créez un service **Application** :
   * **Build Type :** `Dockerfile` (Chemin : `Dockerfile`)
3. Dans l'onglet **Environment** de Dokploy, ajoutez (optionnel, déjà définis par défaut) :
   ```env
   PORT=8085
   ANKI_DECK_NAME=Data Mining
   AUTO_MINE_ON_CLICK=true
   AUTO_DOWNLOAD_JMDICT=true
   ```
4. Dans l'onglet **Mounts (Volumes)** de Dokploy, ajoutez **2 montages** :
   * **Montage 1 (Persistance des données / cartes Anki / dictionnaire) :**
     * Type : `Volume`
     * Volume Name : `yomicalibre-data`
     * Mount Path (dans le conteneur) : `/data`
   * **Montage 2 (Votre bibliothèque Calibre existante sur le serveur) :**
     * Type : `Bind Mount`
     * Host Path : `/chemin/vers/votre/dossier/calibre` *(le même dossier que celui monté dans votre conteneur Calibre-Web)*
     * Mount Path (dans le conteneur) : `/calibre_library`
5. Dans l'onglet **Domains** de Dokploy :
   * Ajoutez votre sous-domaine (ex: `yomi.votredomaine.fr`)
   * **Container Port :** `8085`
   * Activez **HTTPS (Let's Encrypt)** si souhaité.
6. Cliquez sur **Deploy** !

---

### Option B — Déployer en tant que **Compose** dans Dokploy

1. Dans **Dokploy**, créez un service **Compose** et pointez-le vers votre dépôt (ou collez le contenu de `docker-compose.yml`).
2. Dans l'onglet **Environment** du Compose dans Dokploy, indiquez le chemin de votre bibliothèque Calibre sur le serveur :
   ```env
   CALIBRE_HOST_PATH=/chemin/vers/votre/dossier/calibre
   ANKI_DECK_NAME=Data Mining
   AUTO_MINE_ON_CLICK=true
   AUTO_DOWNLOAD_JMDICT=true
   ```
3. Dans l'onglet **Domains** de Dokploy, liez le service `yomicalibre` sur le port **`8085`**.

### Autoriser le pont automatique AnkiConnect sur votre PC portable
Dans Anki sur votre PC portable (*Outils → Greffons → AnkiConnect → Config*), ajoutez l'URL de votre serveur dans `webCorsOriginList` pour que votre navigateur PC puisse vider automatiquement les cartes minées sur votre téléphone dès que vous ouvrez la page :

```json
"webCorsOriginList": [
    "http://localhost",
    "http://127.0.0.1:8085",
    "http://IP_DE_VOTRE_SERVEUR:8085"
]
```
