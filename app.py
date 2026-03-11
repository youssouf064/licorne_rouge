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
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# ---------------- DATABASE ----------------
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    con = sqlite3.connect("database.db")

    con.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        email TEXT,
        password TEXT,
        profile_pic TEXT,
        online INTEGER DEFAULT 0
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS posts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        content TEXT,
        image TEXT,
        date TEXT
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS comments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        post_id INTEGER,
        content TEXT,
        date TEXT
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS likes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        post_id INTEGER
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER,
        receiver_id INTEGER,
        content TEXT,
        date TEXT
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS followers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        follower_id INTEGER,
        following_id INTEGER
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT
    )
    """)

    con.commit()
    con.close()


# ---------------- HOME ----------------
@app.route("/")
def index():
    if "user_id" not in session:
        return redirect("/login")

    con = get_db()

    posts = con.execute("""
        SELECT posts.*, users.username, users.profile_pic,
        (SELECT COUNT(*) FROM likes WHERE post_id = posts.id) AS like_count
        FROM posts
        JOIN users ON posts.user_id = users.id
        ORDER BY posts.id DESC
    """).fetchall()

    comments = con.execute("""
        SELECT comments.*, users.username
        FROM comments
        JOIN users ON comments.user_id = users.id
    """).fetchall()

    con.close()

    return render_template("index.html", posts=posts, comments=comments)


# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET","POST"])
def register():

    if request.method == "POST":

        username = request.form["username"]
        email = request.form["email"]
        password = bcrypt.generate_password_hash(
            request.form["password"]
        ).decode("utf-8")

        profile_pic = request.files.get("profile_pic")
        filename = None

        if profile_pic and profile_pic.filename != "":
            filename = secure_filename(profile_pic.filename)
            profile_pic.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        con = get_db()

        con.execute(
            "INSERT INTO users(username,email,password,profile_pic) VALUES(?,?,?,?)",
            (username,email,password,filename)
        )

        con.commit()
        con.close()

        return redirect("/login")

    return render_template("register.html")


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET","POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        con = get_db()

        user = con.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

        if user and bcrypt.check_password_hash(user["password"], password):

            session["user_id"] = user["id"]
            session["username"] = user["username"]

            con.execute(
                "UPDATE users SET online=1 WHERE id=?",
                (user["id"],)
            )

            con.commit()
            con.close()

            return redirect("/")

        else:
            return "Email ou mot de passe incorrect"

    return render_template("login.html")


# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():

    if "user_id" in session:

        con = get_db()

        con.execute(
            "UPDATE users SET online=0 WHERE id=?",
            (session["user_id"],)
        )

        con.commit()
        con.close()

    session.clear()

    return redirect("/")


# ---------------- RUN ----------------
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)