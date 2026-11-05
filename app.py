import os
import sqlite3
import uuid
from functools import wraps
from datetime import datetime
from urllib.parse import urlparse

from flask import (
    Flask, render_template, request, redirect,
    session, url_for, jsonify
)
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Sur Render, tu pourras mettre DB_PATH vers un disque persistant.
# Sans variable d'environnement, on garde database.db comme avant.
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "database.db"))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "images")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

app = Flask(__name__)

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = "dev-key-change-me"
    if os.environ.get("RENDER"):
        print(
            "ATTENTION : la variable SECRET_KEY n'est pas definie sur Render. "
            "Ajoute-la dans Environment."
        )

app.secret_key = SECRET_KEY
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

# Cookies de session plus sûrs (le mode "secure" n'est activé que sur Render,
# pour que ton serveur local en http continue de fonctionner).
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RENDER"))

bcrypt = Bcrypt(app)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
POSTS_PER_PAGE = 30


def get_db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def redirect_back(default_endpoint="index", **values):
    """Revient à la page précédente, seulement si elle est sur notre site."""
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return redirect(ref)
    return redirect(url_for(default_endpoint, **values))


@app.context_processor
def inject_unread_counts():
    if "user_id" not in session:
        return {
            "unread_count": 0,
            "unread_messages": 0
        }

    con = get_db()

    try:
        unread_count = con.execute("""
            SELECT COUNT(*)
            FROM notifications
            WHERE user_id = ? AND is_read = 0
        """, (session["user_id"],)).fetchone()[0]

        unread_messages = con.execute("""
            SELECT COUNT(*)
            FROM messages
            WHERE receiver_id = ? AND is_read = 0
        """, (session["user_id"],)).fetchone()[0]

    except sqlite3.Error:
        # Un souci de base ne doit pas faire planter toutes les pages.
        unread_count = 0
        unread_messages = 0

    finally:
        con.close()

    return {
        "unread_count": unread_count,
        "unread_messages": unread_messages
    }


def init_db():
    con = get_db()

    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        email TEXT,
        password TEXT,
        profile_pic TEXT,
        online INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        content TEXT,
        image TEXT,
        date TEXT
    );

    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        post_id INTEGER,
        content TEXT,
        date TEXT
    );

    CREATE TABLE IF NOT EXISTS likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        post_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER,
        receiver_id INTEGER,
        content TEXT,
        date TEXT
    );

    CREATE TABLE IF NOT EXISTS followers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        follower_id INTEGER,
        following_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT
    );
    """)

    # Migrations compatibles avec les anciennes bases de données.
    # Elles ajoutent uniquement les colonnes manquantes et conservent les données.
    migrations = {
        "likes": {
            "reaction": "TEXT NOT NULL DEFAULT 'like'"
        },
        "messages": {
            "is_read": "INTEGER NOT NULL DEFAULT 0"
        },
        "notifications": {
            "is_read": "INTEGER NOT NULL DEFAULT 0"
        },
        "users": {
            "bio": "TEXT DEFAULT ''",
            "city": "TEXT DEFAULT ''",
            "cover_pic": "TEXT"
        }
    }

    for table, columns_to_add in migrations.items():
        existing_columns = {
            row["name"]
            for row in con.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, definition in columns_to_add.items():
            if column not in existing_columns:
                con.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )

    # Index : le site reste rapide quand il y a beaucoup de données.
    con.executescript("""
    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id);
    CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
    CREATE INDEX IF NOT EXISTS idx_likes_post ON likes(post_id, user_id);
    CREATE INDEX IF NOT EXISTS idx_messages_pair
        ON messages(sender_id, receiver_id);
    CREATE INDEX IF NOT EXISTS idx_messages_unread
        ON messages(receiver_id, is_read);
    CREATE INDEX IF NOT EXISTS idx_notifications_user
        ON notifications(user_id, is_read);
    CREATE INDEX IF NOT EXISTS idx_followers_pair
        ON followers(follower_id, following_id);
    """)

    con.commit()
    con.close()


init_db()


def _not_logged_in():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Non connecté"}), 401
    return redirect(url_for("login"))


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        user_id = session.get("user_id")

        if not user_id:
            return _not_logged_in()

        # Vérifie que le compte existe encore (utile si la base a été
        # recréée : on évite de rester "connecté" avec un compte fantôme).
        con = get_db()
        try:
            user = con.execute(
                "SELECT id FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        finally:
            con.close()

        if not user:
            session.clear()
            return _not_logged_in()

        return func(*args, **kwargs)
    return wrapper


def save_image(file):
    if not file or not file.filename:
        return None

    original = secure_filename(file.filename)

    if "." not in original:
        return None

    extension = original.rsplit(".", 1)[1].lower()

    if extension not in ALLOWED_EXTENSIONS:
        return None

    filename = f"{uuid.uuid4().hex}.{extension}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))

    return filename


def notify(user_id, message):
    con = get_db()
    con.execute(
        "INSERT INTO notifications(user_id, message) VALUES (?, ?)",
        (user_id, message)
    )
    con.commit()
    con.close()


# ACCUEIL
@app.route("/")
@login_required
def index():
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * POSTS_PER_PAGE

    con = get_db()

    # Récupérer les publications et les réactions
    # (on demande une publication de plus pour savoir s'il y a une page suivante)
    rows = con.execute("""
        SELECT
            posts.*,
            users.username,
            users.profile_pic,

            (SELECT COUNT(*) FROM likes
             WHERE likes.post_id = posts.id) AS like_count,

            (SELECT reaction FROM likes
             WHERE likes.post_id = posts.id
             AND likes.user_id = ?
             ORDER BY id DESC LIMIT 1) AS user_reaction,

            (SELECT COUNT(*) FROM likes
             WHERE likes.post_id = posts.id
             AND reaction = 'like') AS count_like,

            (SELECT COUNT(*) FROM likes
             WHERE likes.post_id = posts.id
             AND reaction = 'love') AS count_love,

            (SELECT COUNT(*) FROM likes
             WHERE likes.post_id = posts.id
             AND reaction = 'haha') AS count_haha,

            (SELECT COUNT(*) FROM likes
             WHERE likes.post_id = posts.id
             AND reaction = 'wow') AS count_wow,

            (SELECT COUNT(*) FROM likes
             WHERE likes.post_id = posts.id
             AND reaction = 'sad') AS count_sad,

            (SELECT COUNT(*) FROM likes
             WHERE likes.post_id = posts.id
             AND reaction = 'angry') AS count_angry

        FROM posts
        JOIN users ON users.id = posts.user_id
        ORDER BY posts.id DESC
        LIMIT ? OFFSET ?
    """, (session["user_id"], POSTS_PER_PAGE + 1, offset)).fetchall()

    has_next = len(rows) > POSTS_PER_PAGE
    posts = rows[:POSTS_PER_PAGE]

    # Récupérer uniquement les commentaires des publications affichées
    post_ids = [post["id"] for post in posts]

    if post_ids:
        placeholders = ",".join("?" * len(post_ids))
        comments = con.execute(f"""
            SELECT comments.*, users.username
            FROM comments
            JOIN users ON users.id = comments.user_id
            WHERE comments.post_id IN ({placeholders})
            ORDER BY comments.id ASC
        """, post_ids).fetchall()
    else:
        comments = []

    # Récupérer le membre connecté
    current_user = con.execute("""
        SELECT id, username, profile_pic
        FROM users
        WHERE id = ?
    """, (session["user_id"],)).fetchone()

    con.close()

    # unread_count et unread_messages sont fournis automatiquement
    # par le context_processor (inject_unread_counts).
    return render_template(
        "index.html",
        posts=posts,
        comments=comments,
        current_user=current_user,
        page=page,
        has_next=has_next
    )


# INSCRIPTION
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or len(password) < 6:
            return "Remplis tous les champs. Mot de passe : 6 caractères minimum.", 400

        if "@" not in email or len(email) > 254 or len(username) > 60:
            return "Email ou nom invalide.", 400

        con = get_db()

        existing = con.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()

        if existing:
            con.close()
            return "Cette adresse email est déjà utilisée.", 400

        password_hash = bcrypt.generate_password_hash(
            password
        ).decode("utf-8")

        try:
            profile_pic = save_image(request.files.get("profile_pic"))
        except Exception:
            con.close()
            return "Image invalide ou trop volumineuse.", 400

        con.execute("""
            INSERT INTO users(username, email, password, profile_pic)
            VALUES (?, ?, ?, ?)
        """, (username, email, password_hash, profile_pic))

        con.commit()
        con.close()

        return redirect(url_for("login"))

    return render_template("register.html")


# CONNEXION
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        con = get_db()
        user = con.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()

        if user and bcrypt.check_password_hash(
            user["password"], password
        ):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]

            con.execute(
                "UPDATE users SET online = 1 WHERE id = ?",
                (user["id"],)
            )
            con.commit()
            con.close()

            return redirect(url_for("index"))

        con.close()
        return "Email ou mot de passe incorrect.", 401

    return render_template("login.html")


# DECONNEXION
@app.route("/logout")
@login_required
def logout():
    con = get_db()
    con.execute(
        "UPDATE users SET online = 0 WHERE id = ?",
        (session["user_id"],)
    )
    con.commit()
    con.close()

    session.clear()
    return redirect(url_for("login"))


# CREER UNE PUBLICATION
@app.route("/create_post", methods=["POST"])
@login_required
def create_post():
    content = request.form.get("content", "").strip()

    try:
        image = save_image(request.files.get("image"))
    except Exception:
        return "Image invalide ou trop volumineuse.", 400

    if not content and not image:
        return "Écris un message ou ajoute une photo.", 400

    con = get_db()
    con.execute("""
        INSERT INTO posts(user_id, content, image, date)
        VALUES (?, ?, ?, ?)
    """, (
        session["user_id"],
        content,
        image,
        datetime.now().strftime("%d/%m/%Y %H:%M")
    ))
    con.commit()
    con.close()

    return redirect(url_for("index"))


# REACTIONS AUX PUBLICATIONS
@app.route("/react/<int:post_id>", methods=["POST"])
@login_required
def react(post_id):
    reactions_autorisees = [
        "like", "love", "haha", "wow", "sad", "angry"
    ]

    reaction = request.form.get("reaction", "").strip()

    if reaction not in reactions_autorisees and reaction != "remove":
        return "Réaction invalide.", 400

    con = get_db()

    post = con.execute(
        "SELECT * FROM posts WHERE id = ?", (post_id,)
    ).fetchone()

    if not post:
        con.close()
        return "Publication introuvable.", 404

    existing = con.execute("""
        SELECT id, reaction FROM likes
        WHERE user_id = ? AND post_id = ?
        ORDER BY id DESC LIMIT 1
    """, (session["user_id"], post_id)).fetchone()

    if reaction == "remove":
        if existing:
            con.execute(
                "DELETE FROM likes WHERE user_id = ? AND post_id = ?",
                (session["user_id"], post_id)
            )

    elif existing:
        con.execute("""
            UPDATE likes SET reaction = ?
            WHERE id = ?
        """, (reaction, existing["id"]))

    else:
        con.execute("""
            INSERT INTO likes (user_id, post_id, reaction)
            VALUES (?, ?, ?)
        """, (session["user_id"], post_id, reaction))

        if post["user_id"] != session["user_id"]:
            con.execute("""
                INSERT INTO notifications(user_id, message)
                VALUES (?, ?)
            """, (
                post["user_id"],
                session["username"] + " a réagi à votre publication."
            ))

    con.commit()
    con.close()

    return redirect_back()


# COMMENTAIRES
@app.route("/comment/<int:post_id>", methods=["POST"])
@login_required
def comment(post_id):
    content = request.form.get("content", "").strip()

    if not content:
        return redirect(url_for("index"))

    con = get_db()

    post = con.execute(
        "SELECT * FROM posts WHERE id = ?", (post_id,)
    ).fetchone()

    if not post:
        con.close()
        return "Publication introuvable.", 404

    con.execute("""
        INSERT INTO comments(user_id, post_id, content, date)
        VALUES (?, ?, ?, ?)
    """, (
        session["user_id"],
        post_id,
        content,
        datetime.now().strftime("%d/%m/%Y %H:%M")
    ))

    if post["user_id"] != session["user_id"]:
        con.execute("""
            INSERT INTO notifications(user_id, message)
            VALUES (?, ?)
        """, (
            post["user_id"],
            session["username"] + " a commenté votre publication."
        ))

    con.commit()
    con.close()

    return redirect_back()


# PROFIL UTILISATEUR
@app.route("/profile/<int:user_id>")
@login_required
def profile(user_id):
    con = get_db()

    user = con.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    if not user:
        con.close()
        return "Utilisateur introuvable.", 404

    posts = con.execute(
        "SELECT * FROM posts WHERE user_id = ? ORDER BY id DESC",
        (user_id,)
    ).fetchall()

    post_list = []

    for post in posts:
        comments = con.execute("""
            SELECT comments.*, users.username
            FROM comments
            JOIN users ON users.id = comments.user_id
            WHERE comments.post_id = ?
            ORDER BY comments.id ASC
        """, (post["id"],)).fetchall()

        item = dict(post)
        item["comments"] = comments
        post_list.append(item)

    followers = con.execute(
        "SELECT COUNT(*) FROM followers WHERE following_id = ?",
        (user_id,)
    ).fetchone()[0]

    following_count = con.execute(
        "SELECT COUNT(*) FROM followers WHERE follower_id = ?",
        (user_id,)
    ).fetchone()[0]

    is_following = con.execute("""
        SELECT id FROM followers
        WHERE follower_id = ? AND following_id = ?
    """, (session["user_id"], user_id)).fetchone()

    con.close()

    return render_template(
        "profile.html",
        user=user,
        posts=post_list,
        followers=followers,
        following_count=following_count,
        is_following=bool(is_following)
    )


# MODIFIER MON PROFIL
@app.route("/edit_profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    con = get_db()

    user = con.execute(
        "SELECT * FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        bio = request.form.get("bio", "").strip()
        city = request.form.get("city", "").strip()

        if not username:
            con.close()
            return "Le nom est obligatoire.", 400

        profile_pic = user["profile_pic"]
        cover_pic = user["cover_pic"]

        try:
            new_profile = save_image(
                request.files.get("profile_pic")
            )
            new_cover = save_image(
                request.files.get("cover_pic")
            )
        except Exception:
            con.close()
            return "Image invalide ou trop volumineuse.", 400

        if new_profile:
            profile_pic = new_profile

        if new_cover:
            cover_pic = new_cover

        con.execute("""
            UPDATE users
            SET username = ?, bio = ?, city = ?,
                profile_pic = ?, cover_pic = ?
            WHERE id = ?
        """, (
            username, bio, city,
            profile_pic, cover_pic,
            session["user_id"]
        ))

        con.commit()
        session["username"] = username
        con.close()

        return redirect(url_for(
            "profile", user_id=session["user_id"]
        ))

    con.close()
    return render_template("edit_profile.html", user=user)


# SUIVRE / NE PLUS SUIVRE
# GET est conservé pour ne pas casser le lien actuel dans profile.html.
# Quand on corrigera profile.html, on passera en POST uniquement.
@app.route("/follow/<int:user_id>", methods=["GET", "POST"])
@login_required
def follow(user_id):
    if user_id == session["user_id"]:
        return redirect(url_for("profile", user_id=user_id))

    con = get_db()

    target = con.execute(
        "SELECT id FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    if not target:
        con.close()
        return "Utilisateur introuvable.", 404

    existing = con.execute("""
        SELECT id FROM followers
        WHERE follower_id = ? AND following_id = ?
    """, (session["user_id"], user_id)).fetchone()

    if existing:
        con.execute(
            "DELETE FROM followers WHERE id = ?", (existing["id"],)
        )
    else:
        con.execute("""
            INSERT INTO followers(follower_id, following_id)
            VALUES (?, ?)
        """, (session["user_id"], user_id))

        con.execute("""
            INSERT INTO notifications(user_id, message)
            VALUES (?, ?)
        """, (
            user_id,
            session["username"] + " vous suit maintenant."
        ))

    con.commit()
    con.close()

    return redirect(url_for("profile", user_id=user_id))


# LISTE DES PERSONNES SUIVIES
@app.route("/following")
@login_required
def following():
    con = get_db()

    users = con.execute("""
        SELECT users.*
        FROM followers
        JOIN users ON users.id = followers.following_id
        WHERE followers.follower_id = ?
    """, (session["user_id"],)).fetchall()

    con.close()
    return render_template("following.html", users=users)


# CENTRE DE NOTIFICATIONS
@app.route("/notifications")
@login_required
def notifications():
    con = get_db()

    notes = con.execute("""
        SELECT *
        FROM notifications
        WHERE user_id = ?
        ORDER BY id DESC
    """, (session["user_id"],)).fetchall()

    # Marquer les notifications comme lues
    con.execute("""
        UPDATE notifications
        SET is_read = 1
        WHERE user_id = ?
    """, (session["user_id"],))

    con.commit()
    con.close()

    return render_template("notifications.html", notes=notes)


# LISTE DES CONVERSATIONS
@app.route("/conversations")
@login_required
def conversations():
    con = get_db()

    users = con.execute("""
        SELECT DISTINCT users.*
        FROM users
        JOIN messages ON (
            users.id = messages.sender_id
            OR users.id = messages.receiver_id
        )
        WHERE users.id != ?
          AND (
            messages.sender_id = ?
            OR messages.receiver_id = ?
          )
    """, (
        session["user_id"],
        session["user_id"],
        session["user_id"]
    )).fetchall()

    con.close()
    return render_template("conversations.html", users=users)


# MESSAGES PRIVES
@app.route("/messages/<int:user_id>", methods=["GET", "POST"])
@login_required
def messages(user_id):
    con = get_db()

    other_user = con.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    if not other_user or user_id == session["user_id"]:
        con.close()
        return "Utilisateur introuvable.", 404

    if request.method == "POST":
        content = request.form.get("content", "").strip()

        if content:
            con.execute("""
                INSERT INTO messages(sender_id, receiver_id, content, date)
                VALUES (?, ?, ?, ?)
            """, (
                session["user_id"],
                user_id,
                content,
                datetime.now().strftime("%d/%m/%Y %H:%M")
            ))
            con.commit()

        con.close()
        return redirect(url_for("messages", user_id=user_id))

    # Marquer comme lus les messages reçus de cet utilisateur
    con.execute("""
        UPDATE messages
        SET is_read = 1
        WHERE sender_id = ? AND receiver_id = ? AND is_read = 0
    """, (user_id, session["user_id"]))

    con.commit()

    # Récupérer les messages de la conversation
    msgs = con.execute("""
        SELECT messages.*, users.username AS sender_name
        FROM messages
        JOIN users ON users.id = messages.sender_id
        WHERE
            (messages.sender_id = ? AND messages.receiver_id = ?)
            OR
            (messages.sender_id = ? AND messages.receiver_id = ?)
        ORDER BY messages.id ASC
    """, (
        session["user_id"], user_id,
        user_id, session["user_id"]
    )).fetchall()

    con.close()

    return render_template(
        "messages.html", msgs=msgs, other_user=other_user
    )


@app.route("/api/messages/<int:user_id>", methods=["GET", "POST"])
@login_required
def messages_api(user_id):
    con = get_db()

    current_id = session["user_id"]

    other_user = con.execute(
        "SELECT id FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    if not other_user or user_id == current_id:
        con.close()
        return jsonify({"error": "Utilisateur introuvable"}), 404

    if request.method == "POST":
        content = request.form.get("content", "").strip()

        if not content:
            con.close()
            return jsonify({"error": "Message vide"}), 400

        if len(content) > 5000:
            con.close()
            return jsonify({"error": "Message trop long"}), 400

        cursor = con.execute("""
            INSERT INTO messages(sender_id, receiver_id, content, date)
            VALUES (?, ?, ?, ?)
        """, (
            current_id,
            user_id,
            content,
            datetime.now().strftime("%d/%m/%Y %H:%M")
        ))

        message_id = cursor.lastrowid
        con.commit()

        message = con.execute("""
            SELECT messages.id, messages.sender_id,
                   messages.content, messages.date,
                   users.username AS sender_name
            FROM messages
            JOIN users ON users.id = messages.sender_id
            WHERE messages.id = ?
        """, (message_id,)).fetchone()

        con.close()
        return jsonify(dict(message)), 201

    # Marquer comme lus les messages reçus
    con.execute("""
        UPDATE messages
        SET is_read = 1
        WHERE sender_id = ? AND receiver_id = ? AND is_read = 0
    """, (user_id, current_id))

    after_id = request.args.get("after_id", 0, type=int)

    messages_list = con.execute("""
        SELECT messages.id, messages.sender_id,
               messages.content, messages.date,
               users.username AS sender_name
        FROM messages
        JOIN users ON users.id = messages.sender_id
        WHERE (
            (messages.sender_id = ? AND messages.receiver_id = ?)
            OR
            (messages.sender_id = ? AND messages.receiver_id = ?)
        )
        AND messages.id > ?
        ORDER BY messages.id ASC
    """, (
        current_id, user_id,
        user_id, current_id,
        after_id
    )).fetchall()

    con.commit()
    con.close()

    return jsonify([dict(message) for message in messages_list])


@app.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()

    users = []

    if query:
        db = get_db()

        # Recherche par nom uniquement : on ne permet plus de deviner
        # l'email des membres.
        users = db.execute("""
            SELECT id, username, profile_pic
            FROM users
            WHERE username LIKE ?
            ORDER BY username ASC
            LIMIT 30
        """, (f"%{query}%",)).fetchall()

        db.close()

    return render_template(
        "search.html",
        users=users,
        query=query
    )


@app.errorhandler(413)
def file_too_large(error):
    return "Fichier trop volumineux. Maximum : 5 Mo.", 413


@app.errorhandler(404)
def page_not_found(error):
    return "Page introuvable.", 404


@app.errorhandler(500)
def server_error(error):
    return "Erreur du serveur. Réessaie dans un instant.", 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)