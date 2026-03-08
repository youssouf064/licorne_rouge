from flask import Flask, render_template, request, redirect, session
import sqlite3
from datetime import datetime
from flask_bcrypt import Bcrypt
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "secret123"
bcrypt = Bcrypt(app)

UPLOAD_FOLDER = "static/images"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------- HOME ----------------
@app.route("/")
def index():
    if "user_id" not in session:
        return redirect("/login")

    con = get_db()

    posts_rows = con.execute("""
        SELECT posts.*, users.username, users.profile_pic,
               (SELECT COUNT(*) FROM likes WHERE post_id = posts.id) AS like_count
        FROM posts
        JOIN users ON posts.user_id = users.id
        ORDER BY posts.id DESC
    """).fetchall()

    comments_rows = con.execute("""
        SELECT comments.*, users.username
        FROM comments
        JOIN users ON comments.user_id = users.id
        ORDER BY comments.id ASC
    """).fetchall()

    # Convertir posts et commentaires en dictionnaires modifiables
    comments_dict = {}
    for c in comments_rows:
        c_dict = dict(c)
        comments_dict.setdefault(c_dict["post_id"], []).append(c_dict)

    posts = []
    for p in posts_rows:
        p_dict = dict(p)
        p_dict["comments"] = comments_dict.get(p_dict["id"], [])
        posts.append(p_dict)

    return render_template("index.html", posts=posts)


# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = bcrypt.generate_password_hash(request.form["password"]).decode("utf-8")

        profile_pic = request.files.get("profile_pic")
        filename = None
        if profile_pic and profile_pic.filename != "":
            filename = secure_filename(profile_pic.filename)
            profile_pic.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        con = get_db()
        con.execute(
            "INSERT INTO users(username,email,password,profile_pic,online) VALUES(?,?,?,?,?)",
            (username, email, password, filename, 1)
        )
        con.commit()
        return redirect("/login")

    return render_template("register.html")


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        con = get_db()
        user = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()

        if user and bcrypt.check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]

            # Mettre en ligne l'utilisateur
            con.execute("UPDATE users SET online=1 WHERE id=?", (user["id"],))
            con.commit()

            return redirect("/")
        else:
            return "Email ou mot de passe incorrect"

    return render_template("login.html")


# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    if "user_id" in session:
        con = get_db()
        con.execute("UPDATE users SET online=0 WHERE id=?", (session["user_id"],))
        con.commit()
    session.clear()
    return redirect("/")


# ---------------- CREATE POST ----------------
@app.route("/create_post", methods=["POST"])
def create_post():
    if "user_id" not in session:
        return redirect("/login")

    content = request.form["content"]
    image = request.files.get("image")
    filename = None
    if image and image.filename != "":
        filename = secure_filename(image.filename)
        image.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

    con = get_db()
    con.execute(
        "INSERT INTO posts(user_id,content,image,date) VALUES(?,?,?,?)",
        (session["user_id"], content, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    con.commit()
    return redirect("/")


# ---------------- LIKE ----------------
@app.route("/like/<int:post_id>")
def like(post_id):
    if "user_id" not in session:
        return redirect("/login")

    con = get_db()
    con.execute("INSERT INTO likes(user_id,post_id) VALUES(?,?)", (session["user_id"], post_id))

    post_owner = con.execute("SELECT user_id FROM posts WHERE id=?", (post_id,)).fetchone()
    if post_owner:
        con.execute("INSERT INTO notifications(user_id,message) VALUES(?,?)",
                    (post_owner["user_id"], "Quelqu'un a aimé votre publication"))
    con.commit()
    return redirect("/")


# ---------------- COMMENT ----------------
@app.route("/comment/<int:post_id>", methods=["POST"])
def comment(post_id):
    if "user_id" not in session:
        return redirect("/login")

    content = request.form["content"]
    con = get_db()
    con.execute(
        "INSERT INTO comments(user_id,post_id,content,date) VALUES(?,?,?,?)",
        (session["user_id"], post_id, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    con.commit()
    return redirect("/")


# ---------------- FOLLOW ----------------
@app.route("/follow/<int:user_id>")
def follow(user_id):
    if "user_id" not in session:
        return redirect("/login")

    con = get_db()
    con.execute("INSERT INTO followers(follower_id,following_id) VALUES(?,?)", (session["user_id"], user_id))
    con.commit()
    return redirect("/")


# ---------------- FOLLOWING LIST ----------------
@app.route("/following")
def following():
    if "user_id" not in session:
        return redirect("/login")

    con = get_db()
    users = con.execute("""
        SELECT users.*
        FROM followers
        JOIN users ON followers.following_id = users.id
        WHERE followers.follower_id = ?
    """, (session["user_id"],)).fetchall()

    return render_template("following.html", users=users)


# ---------------- MESSAGES ----------------
@app.route("/messages/<int:user_id>", methods=["GET","POST"])
def messages(user_id):
    if "user_id" not in session:
        return redirect("/login")

    con = get_db()

    # Envoi du message
    if request.method == "POST":
        content = request.form["content"]
        con.execute(
            "INSERT INTO messages(sender_id,receiver_id,content,date) VALUES(?,?,?,?)",
            (session["user_id"], user_id, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        con.commit()

    # Affichage de la conversation
    msgs = con.execute("""
        SELECT messages.*, u1.username as sender_name, u2.username as receiver_name
        FROM messages
        JOIN users u1 ON messages.sender_id = u1.id
        JOIN users u2 ON messages.receiver_id = u2.id
        WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
        ORDER BY date
    """, (session["user_id"], user_id, user_id, session["user_id"])).fetchall()

    # Récupérer le nom de l'autre utilisateur pour l'afficher en haut
    other_user = con.execute("SELECT id, username FROM users WHERE id=?", (user_id,)).fetchone()

    return render_template("messages.html", msgs=msgs, user_id=user_id, other_user=other_user)

# ---------------- conversations --------------
@app.route("/conversations")
def conversations():
    if "user_id" not in session:
        return redirect("/login")

    con = get_db()

    # Liste des utilisateurs à qui tu peux envoyer des messages
    users = con.execute("""
        SELECT id, username, profile_pic
        FROM users
        WHERE id != ?
    """, (session["user_id"],)).fetchall()

    return render_template("conversations.html", users=users)
    
# ---------------- PROFILE ----------------
@app.route("/profile/<int:user_id>")
def profile(user_id):
    if "user_id" not in session:
        return redirect("/login")

    con = get_db()
    user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        return "Utilisateur non trouvé", 404

    posts_rows = con.execute("""
        SELECT posts.*, (SELECT COUNT(*) FROM likes WHERE post_id = posts.id) AS like_count
        FROM posts WHERE user_id=?
        ORDER BY id DESC
    """, (user_id,)).fetchall()

    posts = []
    for p in posts_rows:
        post_dict = dict(p)
        post_comments = con.execute("""
            SELECT comments.*, users.username
            FROM comments
            JOIN users ON comments.user_id = users.id
            WHERE post_id=?
            ORDER BY id ASC
        """, (p["id"],)).fetchall()
        post_dict["comments"] = [dict(c) for c in post_comments]
        posts.append(post_dict)

    return render_template("profile.html", user=user, posts=posts)


# ---------------- NOTIFICATIONS ----------------
@app.route("/notifications")
def notifications():
    if "user_id" not in session:
        return redirect("/login")

    con = get_db()
    notes_rows = con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC", (session["user_id"],)).fetchall()
    notes = [dict(n) for n in notes_rows]
    return render_template("notifications.html", notes=notes)


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)