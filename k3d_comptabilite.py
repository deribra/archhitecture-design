"""K3D Sénégal - gestion simple des prestations et paiements.

Lancer dans VSCode :  python k3d_comptabilite.py
Installer les dépendances PDF/QR :  python -m pip install -r requirements_comptabilite.txt
La base locale k3d_comptabilite.sqlite3 est créée automatiquement.
"""

from __future__ import annotations

import os
import re
import shutil
import json
import platform
import urllib.request
import urllib.error
import webbrowser
import hashlib
import getpass
import sqlite3
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

DB_FILE = Path(__file__).with_name("k3d_comptabilite.sqlite3")
EXPORT_DIR = Path(__file__).with_name("documents_k3d")
EXPORT_DIR.mkdir(exist_ok=True)
LOGO_SVG = Path(__file__).with_name("logo_k3d.svg")
LOGO_PNG = Path(__file__).with_name("logo_k3d_app.png")
MONTHS = ["Tous les mois", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
          "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
ACTIONS = ["Tous les services", "Offre commerciale", "Projet étude", "Projet de construction"]
# À remplacer par l'URL HTTPS de votre serveur de licences.
LICENSE_API_URL = os.environ.get("K3D_LICENSE_API_URL", "")
PAYDUNYA_PAYMENT_LINK = os.environ.get("K3D_PAYDUNYA_LINK", "")
LICENSE_API_BASE_URL = os.environ.get("K3D_LICENSE_API_BASE_URL", "https://api.k3dsn.com")
WAVE_PHONE = "+221774043234"
ORANGE_MONEY_PHONE = "+221774043234"


def month_number(month_name):
    return "00" if month_name == "Tous les mois" else f"{MONTHS.index(month_name):02d}"


def valid_license_format(value):
    return len(value) == 12 and value.isalnum()


def check_online_license(license_key):
    if not LICENSE_API_URL:
        return False, "Serveur de licences non configuré. Définissez K3D_LICENSE_API_URL."
    device_id = hashlib.sha256(f"{platform.node()}-{getpass.getuser()}".encode()).hexdigest()
    payload = json.dumps({"license": license_key, "device_id": device_id, "application": "K3D-Comptabilite"}).encode()
    request = urllib.request.Request(LICENSE_API_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
        return bool(result.get("valid")), result.get("message", "Licence refusée.")
    except Exception as error:
        return False, f"Serveur de licences inaccessible : {error}"


def create_license_payment(plan, name, email, phone):
    """Ask the Render API to create a PayDunya checkout invoice."""
    payload = json.dumps({
        "plan": plan,
        "customer": {"name": name, "email": email, "phone": phone},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LICENSE_API_BASE_URL.rstrip('/')}/api/create-payment",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Render Free peut mettre jusqu'à une minute à réveiller le service.
        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("payment_url"):
            return None, result.get("error", "PayDunya n'a pas retourné de lien de paiement.")
        return result, None
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(details).get("error", details)
        except json.JSONDecodeError:
            pass
        return None, f"API paiement HTTP {error.code} : {details}"
    except Exception as error:
        return None, f"Impossible de contacter l'API de paiement : {error}"


def get_payment_status(token):
    req = urllib.request.Request(f"{LICENSE_API_BASE_URL.rstrip('/')}/api/payment-status/{token}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def create_wave_license(plan, name, email, phone, admin_secret):
    """Register a manually confirmed Wave payment and create its licence."""
    payload = json.dumps({
        "plan": plan,
        "customer": {"name": name, "email": email, "phone": phone},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LICENSE_API_BASE_URL.rstrip('/')}/api/admin/create-wave-license",
        data=payload,
        headers={"Content-Type": "application/json", "X-Admin-Secret": admin_secret},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result, None
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(details).get("error", details)
        except json.JSONDecodeError:
            pass
        return None, f"API Wave HTTP {error.code} : {details}"
    except Exception as error:
        return None, f"Impossible de contacter l'API Wave : {error}"


def date_to_iso(value):
    """Convert the French display format to the database ISO format."""
    value = value.strip()
    if not value:
        return ""
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Date invalide")


def date_to_french(value):
    if not value:
        return ""
    try:
        return date.fromisoformat(value).strftime("%d/%m/%Y")
    except ValueError:
        return value


class LicenseGate(tk.Tk):
    """First screen: the application opens only after online validation."""
    def __init__(self):
        super().__init__()
        self.title("K3D Sénégal — Activation")
        self.geometry("560x500")
        self.resizable(False, False)
        self.license_ok = False
        self.license_var = tk.StringVar()
        ttk.Label(self, text="K3D Sénégal", font=("Arial", 23, "bold")).pack(pady=(28, 5))
        ttk.Label(self, text="Entrez votre licence de 12 lettres et chiffres").pack()
        entry = ttk.Entry(self, textvariable=self.license_var, width=28, justify="center")
        entry.pack(pady=12); entry.focus_set()
        ttk.Button(self, text="Activer l'application", command=self.activate).pack()
        self.message = ttk.Label(self, text="", foreground="#b00020", wraplength=440)
        self.message.pack(pady=10)
        purchase = ttk.LabelFrame(self, text="Achat d'une licence", padding=10)
        purchase.pack(fill="x", padx=25, pady=8)
        ttk.Label(purchase, text="PayDunya permet de choisir Wave, Orange Money ou carte.").pack(anchor="w")
        self.purchase_plan = tk.StringVar(value="1 mois — 2 500 FCFA")
        self.purchase_name = tk.StringVar(); self.purchase_email = tk.StringVar(); self.purchase_phone = tk.StringVar()
        fields = [("Durée", self.purchase_plan), ("Nom complet", self.purchase_name), ("Email", self.purchase_email), ("Téléphone", self.purchase_phone)]
        for label, variable in fields:
            row = ttk.Frame(purchase); row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=15).pack(side="left")
            if label == "Durée":
                ttk.Combobox(row, textvariable=variable, state="readonly", values=("1 mois — 2 500 FCFA", "1 an — 20 000 FCFA"), width=30).pack(side="left")
            else:
                ttk.Entry(row, textvariable=variable, width=34).pack(side="left")
        buttons = ttk.Frame(purchase)
        buttons.pack(pady=8)
        ttk.Button(buttons, text="Payer avec Wave", command=lambda: self.mobile_money("Wave")).pack(side="left", padx=4)
        ttk.Button(buttons, text="Payer avec PayDunya", command=self.paydunya).pack(side="left", padx=4)

    def activate(self):
        key = self.license_var.get().strip().upper()
        if not valid_license_format(key):
            self.message.config(text="La licence doit contenir exactement 12 lettres et chiffres.")
            return
        self.message.config(text="Vérification en ligne..."); self.update_idletasks()
        ok, message = check_online_license(key)
        if ok:
            self.license_ok = True
            self.destroy()
        else:
            self.message.config(text=message)

    def mobile_money(self, provider):
        number = WAVE_PHONE if provider == "Wave" else ORANGE_MONEY_PHONE
        plan = getattr(self, "purchase_plan", None)
        plan_text = plan.get() if plan else "1 mois — 2 500 FCFA"
        amount = "20 000 FCFA" if plan_text.startswith("1 an") else "2 500 FCFA"
        messagebox.showinfo(
            f"Paiement avec {provider}",
            f"Envoyez {amount} avec {provider} au numéro :\n\n{number}\n\n"
            "Vous recevrez votre code de licence dans un délai maximum de 15 minutes."
        )

    def paydunya(self):
        if not self.purchase_name.get().strip() or not self.purchase_email.get().strip():
            messagebox.showwarning("PayDunya", "Le nom complet et l'email sont obligatoires.")
            return
        plan = "year" if self.purchase_plan.get().startswith("1 an") else "month"
        result, error = create_license_payment(plan, self.purchase_name.get().strip(), self.purchase_email.get().strip(), self.purchase_phone.get().strip())
        if error:
            messagebox.showerror("PayDunya", error)
            return
        webbrowser.open(result["payment_url"])
        self.message.config(text=f"Paiement ouvert. Référence : {result['reference']}", foreground="#126b2e")
        self.payment_attempts = 0
        self.after(15000, lambda: self.check_payment(result["token"]))

    def check_payment(self, token):
        self.payment_attempts += 1
        result = get_payment_status(token)
        if result and result.get("status") == "completed" and result.get("license"):
            self.message.config(text=f"Paiement confirmé. Votre licence est : {result['license']}", foreground="#126b2e")
        elif self.payment_attempts < 20:
            self.after(15000, lambda: self.check_payment(token))


def db_connection():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def init_database():
    with db_connection() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '',
            address TEXT, phone TEXT, email TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL,
            reference TEXT NOT NULL UNIQUE, description TEXT, total REAL NOT NULL,
            advance REAL NOT NULL DEFAULT 0, next_payment REAL NOT NULL DEFAULT 0,
            balance REAL NOT NULL DEFAULT 0, year INTEGER NOT NULL DEFAULT 0,
            search_text TEXT NOT NULL DEFAULT '', lot TEXT NOT NULL DEFAULT 'Les deux',
            project_folder TEXT NOT NULL DEFAULT '', action_type TEXT NOT NULL DEFAULT 'Projet étude', created_at TEXT NOT NULL,
            FOREIGN KEY(client_id) REFERENCES clients(id)
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL,
            phase TEXT NOT NULL, amount REAL NOT NULL, paid_at TEXT NOT NULL,
            FOREIGN KEY(invoice_id) REFERENCES invoices(id)
        );
        CREATE TABLE IF NOT EXISTS deliverables (
            id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL,
            name TEXT NOT NULL, submitted TEXT NOT NULL DEFAULT 'Pas encore soumis',
            start_date TEXT, end_date TEXT, completion_date TEXT,
            FOREIGN KEY(invoice_id) REFERENCES invoices(id)
        );
        """)
        # Migration for databases created with an earlier version.
        columns = {row[1] for row in con.execute("PRAGMA table_info(invoices)")}
        if "year" not in columns:
            con.execute("ALTER TABLE invoices ADD COLUMN year INTEGER NOT NULL DEFAULT 0")
        if "search_text" not in columns:
            con.execute("ALTER TABLE invoices ADD COLUMN search_text TEXT NOT NULL DEFAULT ''")
        if "lot" not in columns:
            con.execute("ALTER TABLE invoices ADD COLUMN lot TEXT NOT NULL DEFAULT 'Les deux'")
        if "project_folder" not in columns:
            con.execute("ALTER TABLE invoices ADD COLUMN project_folder TEXT NOT NULL DEFAULT ''")
        if "action_type" not in columns:
            con.execute("ALTER TABLE invoices ADD COLUMN action_type TEXT NOT NULL DEFAULT 'Projet étude'")
        client_columns = {row[1] for row in con.execute("PRAGMA table_info(clients)")}
        if "first_name" not in client_columns:
            con.execute("ALTER TABLE clients ADD COLUMN first_name TEXT NOT NULL DEFAULT ''")
        if "last_name" not in client_columns:
            con.execute("ALTER TABLE clients ADD COLUMN last_name TEXT NOT NULL DEFAULT ''")
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('website','')")


def money(value):
    return f"{float(value):,.0f} FCFA".replace(",", " ")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("K3D Sénégal — Comptabilité")
        self.set_application_logo()
        self.geometry("1050x720")
        self.minsize(900, 620)
        self.client_id = None
        self.phase_vars = []
        self._build_ui()
        self.vars["year"].set(str(date.today().year))
        self.refresh_list()

    def set_application_logo(self):
        """Use a reduced version of the K3D logo as the application icon."""
        try:
            import cairosvg
            cairosvg.svg2png(url=str(LOGO_SVG), write_to=str(LOGO_PNG), output_width=64, output_height=64)
            self.logo_image = tk.PhotoImage(file=str(LOGO_PNG))
            self.iconphoto(True, self.logo_image)
        except Exception:
            # The application remains usable if SVG conversion is unavailable.
            self.logo_image = None

    def build_purchase_tab(self):
        ttk.Label(self.purchase_tab, text="Achat d'une licence", font=("Arial", 18, "bold")).pack(anchor="w")
        ttk.Label(self.purchase_tab, text="Après confirmation du paiement, une licence de 12 caractères vous sera délivrée.", wraplength=800).pack(anchor="w", pady=12)
        box = ttk.LabelFrame(self.purchase_tab, text="Moyens de paiement", padding=15)
        box.pack(anchor="w", fill="x")
        self.app_purchase_plan = tk.StringVar(value="1 mois — 2 500 FCFA")
        self.app_purchase_name = tk.StringVar(); self.app_purchase_email = tk.StringVar(); self.app_purchase_phone = tk.StringVar()
        for label, variable in (("Durée", self.app_purchase_plan), ("Nom complet", self.app_purchase_name), ("Email", self.app_purchase_email), ("Téléphone", self.app_purchase_phone)):
            row = ttk.Frame(box); row.pack(anchor="w", pady=3)
            ttk.Label(row, text=label, width=15).pack(side="left")
            if label == "Durée":
                ttk.Combobox(row, textvariable=variable, state="readonly", values=("1 mois — 2 500 FCFA", "1 an — 20 000 FCFA"), width=30).pack(side="left")
            else:
                ttk.Entry(row, textvariable=variable, width=34).pack(side="left")
        ttk.Label(box, text="Wave est disponible par paiement direct au numéro ci-dessous.").pack(anchor="w", pady=5)
        buttons = ttk.Frame(box)
        buttons.pack(anchor="w", pady=10)
        ttk.Button(buttons, text="Payer avec Wave", command=self.open_wave_payment).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Payer avec PayDunya", command=self.open_paydunya_payment).pack(side="left")
        ttk.Button(box, text="Administrateur : confirmer Wave et générer la licence", command=self.confirm_wave_payment).pack(anchor="w", pady=(2, 0))

    def open_wave_payment(self):
        plan_text = self.app_purchase_plan.get()
        amount = "20 000 FCFA" if plan_text.startswith("1 an") else "2 500 FCFA"
        messagebox.showinfo(
            "Paiement avec Wave",
            f"Envoyez {amount} avec Wave au numéro :\n\n{WAVE_PHONE}\n\n"
            "Vous recevrez votre code de licence dans un délai maximum de 15 minutes."
        )

    def confirm_wave_payment(self):
        if not self.app_purchase_name.get().strip():
            messagebox.showwarning("Licence Wave", "Renseignez d'abord le nom du client.")
            return
        if not messagebox.askyesno("Confirmation Wave", "Avez-vous bien reçu le paiement Wave ?"):
            return
        admin_secret = simpledialog.askstring("Administrateur", "Entrez votre clé administrateur :", show="*")
        if not admin_secret:
            return
        plan = "year" if self.app_purchase_plan.get().startswith("1 an") else "month"
        result, error = create_wave_license(
            plan,
            self.app_purchase_name.get().strip(),
            self.app_purchase_email.get().strip(),
            self.app_purchase_phone.get().strip(),
            admin_secret,
        )
        if error:
            messagebox.showerror("Licence Wave", error)
            return
        messagebox.showinfo(
            "Licence Wave créée",
            f"Licence : {result['license']}\n\n"
            f"Valable jusqu'au : {result['expires_at'][:10]}\n\n"
            "Envoyez ce code au client par WhatsApp ou email.",
        )

    def open_paydunya_payment(self):
        if not self.app_purchase_name.get().strip() or not self.app_purchase_email.get().strip():
            messagebox.showwarning("PayDunya", "Le nom complet et l'email sont obligatoires.")
            return
        plan = "year" if self.app_purchase_plan.get().startswith("1 an") else "month"
        result, error = create_license_payment(plan, self.app_purchase_name.get().strip(), self.app_purchase_email.get().strip(), self.app_purchase_phone.get().strip())
        if error:
            messagebox.showerror("PayDunya", error)
            return
        webbrowser.open(result["payment_url"])
        messagebox.showinfo("PayDunya", f"Page de paiement ouverte.\nRéférence : {result['reference']}\nAprès paiement, votre licence sera générée.")

    def _build_ui(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.main_tab = ttk.Frame(self.notebook)
        self.upload_tab = ttk.Frame(self.notebook, padding=18)
        self.management_tab = ttk.Frame(self.notebook, padding=18)
        self.planning_tab = ttk.Frame(self.notebook, padding=18)
        self.report_tab = ttk.Frame(self.notebook, padding=18)
        self.purchase_tab = ttk.Frame(self.notebook, padding=18)
        self.notebook.add(self.main_tab, text="Comptabilité")
        self.notebook.add(self.upload_tab, text="Documents client")
        self.notebook.add(self.management_tab, text="Gestion")
        self.notebook.add(self.planning_tab, text="Planning")
        self.notebook.add(self.report_tab, text="Rapport")
        self.notebook.add(self.purchase_tab, text="Achat")

        top = ttk.Frame(self.main_tab, padding=12)
        top.pack(fill="x")
        if self.logo_image:
            ttk.Label(top, image=self.logo_image).pack(side="left", padx=(0, 8))
        ttk.Label(top, text="K3D Sénégal", font=("Arial", 22, "bold")).pack(side="left")
        ttk.Button(top, text="Paramètres / site web", command=self.settings).pack(side="right")

        form = ttk.LabelFrame(self.main_tab, text="Nouvelle prestation", padding=12)
        form.pack(fill="x", padx=12, pady=5)
        self.vars = {k: tk.StringVar() for k in (
            "first_name", "last_name", "address", "phone", "email", "description", "total", "year", "lot",
            "advance", "next_payment", "balance")}
        fields = [("Prénom", "first_name"), ("Nom", "last_name"),
                  ("Adresse", "address"),
                  ("Téléphone", "phone"), ("Email", "email"),
                  ("Description", "description"), ("Montant total (FCFA)", "total"),
                  ("Année", "year"), ("Lot", "lot")]
        for i, (label, key) in enumerate(fields):
            r, c = divmod(i, 3)
            ttk.Label(form, text=label).grid(row=r * 2, column=c, sticky="w", padx=5, pady=(2, 0))
            ttk.Entry(form, textvariable=self.vars[key], width=34).grid(row=r * 2 + 1, column=c, sticky="ew", padx=5, pady=(0, 5))
        for c in range(3):
            form.columnconfigure(c, weight=1)
        self.vars["lot"].set("Les deux")
        lot_row, lot_col = divmod(fields.index(("Lot", "lot")), 3)
        lot_entry = form.grid_slaves(row=lot_row * 2 + 1, column=lot_col)[0]
        lot_entry.destroy()
        ttk.Combobox(form, textvariable=self.vars["lot"], values=("Architecture", "Structure", "Les deux"), state="readonly", width=31).grid(row=lot_row * 2 + 1, column=lot_col, sticky="ew", padx=5, pady=(0, 5))

        self.action_type_var = tk.StringVar(value="Projet étude")
        action_band = ttk.LabelFrame(self.main_tab, text="Type d'action souhaité", padding=8)
        action_band.pack(fill="x", padx=12, pady=5)
        ttk.Label(action_band, text="Choisissez avant de renseigner les paiements :").pack(side="left", padx=8)
        self.action_combo = ttk.Combobox(action_band, textvariable=self.action_type_var, state="readonly", width=30,
                                         values=("Offre commerciale", "Projet étude", "Projet de construction"))
        self.action_combo.pack(side="left", padx=8)
        self.action_combo.bind("<<ComboboxSelected>>", lambda _event: self.toggle_payment_fields())

        self.phase = ttk.LabelFrame(self.main_tab, text="Échéancier — paiements", padding=10)
        self.phase.pack(fill="x", padx=12, pady=5)
        self.phase_vars = [("Avance", self.vars["advance"]), ("Paiement 2", self.vars["next_payment"]), ("Solde", self.vars["balance"])]
        self.render_phases()
        self.toggle_payment_fields()

        actions = ttk.Frame(self.main_tab, padding=12)
        actions.pack(fill="x")
        ttk.Button(actions, text="Valider", command=self.save).pack(side="left", padx=4)
        ttk.Button(actions, text="Générer le PDF", command=self.make_pdf).pack(side="left", padx=4)
        ttk.Button(actions, text="Supprimer le projet", command=self.delete_selected_project).pack(side="left", padx=4)
        ttk.Button(actions, text="Vider le formulaire", command=self.clear_form).pack(side="left", padx=4)

        box = ttk.LabelFrame(self.main_tab, text="Prestations enregistrées", padding=8)
        box.pack(fill="both", expand=True, padx=12, pady=5)
        searchbar = ttk.Frame(box)
        searchbar.pack(fill="x", pady=(0, 6))
        ttk.Label(searchbar, text="Rechercher :").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(searchbar, textvariable=self.search_var, width=45)
        search_entry.pack(side="left", padx=6)
        search_entry.bind("<Return>", lambda _e: self.refresh_list())
        ttk.Button(searchbar, text="Rechercher", command=self.refresh_list).pack(side="left")
        self.month_var = tk.StringVar(value="Tous les mois")
        ttk.Label(searchbar, text="Mois :").pack(side="left", padx=(14, 3))
        month_filter = ttk.Combobox(searchbar, textvariable=self.month_var, values=MONTHS, state="readonly", width=16)
        month_filter.pack(side="left")
        month_filter.bind("<<ComboboxSelected>>", lambda _event: self.refresh_list())
        self.action_filter_var = tk.StringVar(value="Tous les services")
        ttk.Label(searchbar, text="Service :").pack(side="left", padx=(10, 3))
        action_filter = ttk.Combobox(searchbar, textvariable=self.action_filter_var, values=ACTIONS, state="readonly", width=23)
        action_filter.pack(side="left")
        action_filter.bind("<<ComboboxSelected>>", lambda _event: self.refresh_list())
        ttk.Button(searchbar, text="Effacer", command=lambda: (self.search_var.set(""), self.refresh_list())).pack(side="left", padx=4)
        columns = ("id", "reference", "client", "lot", "total", "paid", "remaining", "date")
        self.tree = ttk.Treeview(box, columns=columns, show="headings")
        headings = {"id": "ID", "reference": "Référence", "client": "Client", "lot": "Lot", "total": "Total", "paid": "Payé", "remaining": "Reste", "date": "Date"}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=115 if col not in ("client", "lot") else 160)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.select_invoice)
        self.build_upload_tab()
        self.build_management_tab()
        self.build_planning_tab()
        self.build_report_tab()
        self.build_purchase_tab()

    def build_upload_tab(self):
        ttk.Label(self.upload_tab, text="Documents du client", font=("Arial", 18, "bold")).pack(anchor="w")
        ttk.Label(self.upload_tab, text="Sélectionnez un projet puis envoyez les fichiers dans son dossier 02- Données du client.").pack(anchor="w", pady=(4, 15))
        line = ttk.Frame(self.upload_tab)
        line.pack(fill="x")
        ttk.Label(line, text="Projet :").pack(side="left")
        self.project_var = tk.StringVar()
        self.project_combo = ttk.Combobox(line, textvariable=self.project_var, state="readonly", width=65)
        self.project_combo.pack(side="left", padx=8)
        self.project_combo.bind("<<ComboboxSelected>>", lambda _event: self.show_project_documents())
        self.upload_month_var = tk.StringVar(value="Tous les mois")
        ttk.Label(line, text="Mois :").pack(side="left", padx=(12, 3))
        upload_month = ttk.Combobox(line, textvariable=self.upload_month_var, values=MONTHS, state="readonly", width=15)
        upload_month.pack(side="left")
        upload_month.bind("<<ComboboxSelected>>", lambda _event: self.refresh_upload_projects())
        self.upload_action_var = tk.StringVar(value="Tous les services")
        ttk.Label(line, text="Service :").pack(side="left", padx=(10, 3))
        upload_action = ttk.Combobox(line, textvariable=self.upload_action_var, values=ACTIONS, state="readonly", width=21)
        upload_action.pack(side="left")
        upload_action.bind("<<ComboboxSelected>>", lambda _event: self.refresh_upload_projects())
        ttk.Button(line, text="Actualiser", command=self.refresh_upload_projects).pack(side="left")
        ttk.Button(self.upload_tab, text="+ Uploader des documents", command=self.upload_documents).pack(anchor="w", pady=15)
        self.uploaded_list = tk.Listbox(self.upload_tab, height=16)
        self.uploaded_list.pack(fill="both", expand=True)

    def refresh_upload_projects(self):
        if not hasattr(self, "project_combo"):
            return
        month = month_number(self.upload_month_var.get())
        action = self.upload_action_var.get()
        with db_connection() as con:
            rows = con.execute("""SELECT id,reference,project_folder FROM invoices
                WHERE (?='00' OR substr(created_at,6,2)=?)
                  AND (?='Tous les services' OR action_type=?) ORDER BY id DESC""", (month, month, action, action)).fetchall()
        self.project_map = {f"{r['reference']} — {r['project_folder']}": r["project_folder"] for r in rows}
        self.project_combo["values"] = list(self.project_map)
        if self.project_map and self.project_var.get() not in self.project_map:
            self.project_var.set(next(iter(self.project_map)))
        self.show_project_documents()

    def show_project_documents(self):
        """Display the selected project's client-document folder."""
        if not hasattr(self, "uploaded_list"):
            return
        self.uploaded_list.delete(0, tk.END)
        folder = self.project_map.get(self.project_var.get()) if hasattr(self, "project_map") else None
        if not folder:
            return
        destination = Path(folder) / "02- Données du client"
        destination.mkdir(parents=True, exist_ok=True)
        for item in sorted(destination.iterdir()):
            if item.is_file():
                self.uploaded_list.insert(tk.END, item.name)

    def upload_documents(self):
        selected_project = self.project_var.get()
        if not selected_project or selected_project not in self.project_map:
            messagebox.showwarning("Projet", "Sélectionnez d'abord un projet.")
            return
        files = filedialog.askopenfilenames(title="Choisir les documents du client")
        if not files:
            return
        destination = Path(self.project_map[selected_project]) / "02- Données du client"
        destination.mkdir(parents=True, exist_ok=True)
        copied = []
        for source in files:
            target = destination / Path(source).name
            shutil.copy2(source, target)
            copied.append(target.name)
        self.uploaded_list.delete(0, tk.END)
        for name in sorted(destination.iterdir()):
            if name.is_file():
                self.uploaded_list.insert(tk.END, name.name)
        messagebox.showinfo("Documents ajoutés", f"{len(copied)} document(s) ajouté(s) dans :\n{destination}")

    def build_management_tab(self):
        ttk.Label(self.management_tab, text="Gestion des projets", font=("Arial", 18, "bold")).pack(anchor="w")
        line = ttk.Frame(self.management_tab)
        line.pack(fill="x", pady=(12, 8))
        ttk.Label(line, text="Afficher :").pack(side="left")
        self.status_var = tk.StringVar(value="Tous les projets")
        status = ttk.Combobox(line, textvariable=self.status_var, state="readonly", width=30,
                              values=("Tous les projets", "Projets payés — soldés à 100 %", "Projets à solder"))
        status.pack(side="left", padx=8)
        status.bind("<<ComboboxSelected>>", lambda _event: self.refresh_management())
        self.management_month_var = tk.StringVar(value="Tous les mois")
        ttk.Label(line, text="Mois :").pack(side="left", padx=(12, 3))
        management_month = ttk.Combobox(line, textvariable=self.management_month_var, values=MONTHS, state="readonly", width=15)
        management_month.pack(side="left")
        management_month.bind("<<ComboboxSelected>>", lambda _event: self.refresh_management())
        self.management_action_var = tk.StringVar(value="Tous les services")
        ttk.Label(line, text="Service :").pack(side="left", padx=(10, 3))
        management_action = ttk.Combobox(line, textvariable=self.management_action_var, values=ACTIONS, state="readonly", width=21)
        management_action.pack(side="left")
        management_action.bind("<<ComboboxSelected>>", lambda _event: self.refresh_management())
        ttk.Button(line, text="Actualiser", command=self.refresh_management).pack(side="left")
        ttk.Button(line, text="📄 Contrat", command=self.open_selected_contract).pack(side="left", padx=8)
        columns = ("reference", "client", "phone", "total", "paid", "remaining", "status")
        self.management_tree = ttk.Treeview(self.management_tab, columns=columns, show="headings")
        headings = {"reference": "Référence", "client": "Nom du client", "phone": "Téléphone",
                    "total": "Montant total", "paid": "Montant payé", "remaining": "Reste", "status": "État"}
        for col in columns:
            self.management_tree.heading(col, text=headings[col])
            self.management_tree.column(col, width=150 if col != "client" else 220)
        self.management_tree.pack(fill="both", expand=True)
        self.management_total_var = tk.StringVar(value="Projets affichés : 0")
        self.management_paid_var = tk.StringVar(value="Montant total payé : 0 FCFA")
        self.management_remaining_var = tk.StringVar(value="Montant total à solder : 0 FCFA")
        totals = ttk.Frame(self.management_tab)
        totals.pack(fill="x", pady=(8, 0))
        ttk.Label(totals, textvariable=self.management_total_var, font=("Arial", 11, "bold")).pack(side="left", padx=8)
        ttk.Label(totals, textvariable=self.management_paid_var, font=("Arial", 11, "bold")).pack(side="left", padx=8)
        ttk.Label(totals, textvariable=self.management_remaining_var, font=("Arial", 11, "bold")).pack(side="right", padx=8)

    def open_selected_contract(self):
        selected = self.management_tree.selection()
        if not selected:
            messagebox.showwarning("Contrat", "Sélectionnez d'abord un projet.")
            return
        reference = self.management_tree.item(selected[0], "values")[0]
        with db_connection() as con:
            row = con.execute("SELECT project_folder FROM invoices WHERE reference=?", (reference,)).fetchone()
        if not row:
            messagebox.showerror("Contrat", "Projet introuvable.")
            return
        contract = Path(row["project_folder"]) / "01- Gestion-contrat" / "contrat.docx"
        if not contract.exists():
            messagebox.showwarning("Contrat", "Le contrat n'existe pas pour ce projet.")
            return
        try:
            os.startfile(contract)
        except OSError as error:
            messagebox.showerror("Contrat", f"Impossible d'ouvrir le contrat : {error}")

    def create_contract_docx(self, project_folder, reference, first_name, last_name,
                             address, phone, email, description, lot, total,
                             amounts, year):
        """Créer le contrat Word dans le dossier 01- Gestion-contrat."""
        try:
            from docx import Document
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.shared import Pt
        except ImportError:
            messagebox.showwarning("Contrat non créé", "Installez les dépendances avec : python -m pip install -r requirements_comptabilite.txt")
            return
        folder = Path(project_folder) / "01- Gestion-contrat"
        folder.mkdir(parents=True, exist_ok=True)
        filename = folder / "contrat.docx"
        doc = Document()
        doc.styles["Normal"].font.name = "Arial"
        doc.styles["Normal"].font.size = Pt(10)
        title = doc.add_paragraph(); title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run("CONTRAT DE PRESTATION DE SERVICES")
        run.bold = True; run.font.size = Pt(15)
        subtitle = doc.add_paragraph(); subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
        subtitle.add_run(f"Référence : {reference} — Année {year}").italic = True
        doc.add_heading("Entre les soussignés", level=2)
        doc.add_paragraph(
            "K3D Sénégal, représentée par son gérant M. Ibra DER, ci-après dénommée « le Prestataire »,\n"
            "et\n"
            f"M./Mme {first_name} {last_name}, domicilié(e) à {address or '................................'}, "
            f"téléphone : {phone or '................................'}, email : {email or '................................'}, "
            "ci-après dénommé(e) « le Client »."
        )
        doc.add_heading("Article 1 — Objet de la prestation", level=2)
        doc.add_paragraph(f"Le Prestataire réalise pour le Client une prestation relevant du lot « {lot} ». Description : {description or '................................' }.")
        doc.add_heading("Article 2 — Montant et échéancier", level=2)
        doc.add_paragraph(f"Le montant total convenu est de {money(total)}.")
        table = doc.add_table(rows=1, cols=2); table.style = "Table Grid"
        table.rows[0].cells[0].text = "Phase"; table.rows[0].cells[1].text = "Montant prévu"
        for phase, amount in amounts:
            cells = table.add_row().cells; cells[0].text = phase; cells[1].text = money(amount)
        doc.add_heading("Article 3 — Obligations des parties", level=2)
        doc.add_paragraph("Le Prestataire réalise la mission avec diligence et remet les livrables convenus. Le Client fournit les informations nécessaires, valide les étapes et règle les montants prévus à l’échéancier.")
        doc.add_heading("Article 4 — Modifications", level=2)
        doc.add_paragraph("Toute modification importante de la mission ou des livrables fera l’objet d’un accord écrit entre les parties et pourra entraîner une adaptation du délai et du montant.")
        doc.add_heading("Article 5 — Signatures", level=2)
        doc.add_paragraph(f"Fait à ____________________, le ____ / ____ / {year}.\n\nSignature du Client :\n\n\nSignature du Prestataire :\nK3D Sénégal — Ibra DER")
        doc.save(filename)

    def refresh_management(self):
        if not hasattr(self, "management_tree"):
            return
        for item in self.management_tree.get_children():
            self.management_tree.delete(item)
        month = month_number(self.management_month_var.get())
        action = self.management_action_var.get()
        with db_connection() as con:
            rows = con.execute("""SELECT i.id,i.reference,c.name,c.phone,i.total,
                COALESCE(SUM(p.amount),0) paid
                FROM invoices i JOIN clients c ON c.id=i.client_id
                LEFT JOIN payments p ON p.invoice_id=i.id
                WHERE (?='00' OR substr(i.created_at,6,2)=?)
                  AND (?='Tous les services' OR i.action_type=?)
                GROUP BY i.id ORDER BY i.id DESC""", (month, month, action, action)).fetchall()
        choice = self.status_var.get()
        total_displayed = 0.0
        paid_displayed = 0.0
        remaining_displayed = 0.0
        count_displayed = 0
        for row in rows:
            paid = float(row["paid"])
            total = float(row["total"])
            remaining = max(0, total - paid)
            is_paid = paid >= total and total > 0
            state = "Payé — soldé à 100 %" if is_paid else "À solder"
            if choice == "Projets payés — soldés à 100 %" and not is_paid:
                continue
            if choice == "Projets à solder" and is_paid:
                continue
            total_displayed += total
            paid_displayed += paid
            remaining_displayed += remaining
            count_displayed += 1
            self.management_tree.insert("", "end", values=(row["reference"], row["name"], row["phone"] or "", money(total), money(paid), money(remaining), state))
        self.management_total_var.set(f"{count_displayed} projet(s) affiché(s) — Total projets : {money(total_displayed)}")
        self.management_paid_var.set(f"Total payé : {money(paid_displayed)}")
        self.management_remaining_var.set(f"Total à solder : {money(remaining_displayed)}")

    def build_planning_tab(self):
        ttk.Label(self.planning_tab, text="Planning des livrables", font=("Arial", 18, "bold")).pack(anchor="w")
        selector = ttk.Frame(self.planning_tab)
        selector.pack(fill="x", pady=(12, 8))
        ttk.Label(selector, text="Projet :").pack(side="left")
        self.planning_project_var = tk.StringVar()
        self.planning_project_combo = ttk.Combobox(selector, textvariable=self.planning_project_var, state="readonly", width=58)
        self.planning_project_combo.pack(side="left", padx=8)
        self.planning_project_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_planning())
        self.planning_month_var = tk.StringVar(value="Tous les mois")
        ttk.Label(selector, text="Mois :").pack(side="left", padx=(12, 3))
        planning_month = ttk.Combobox(selector, textvariable=self.planning_month_var, values=MONTHS, state="readonly", width=15)
        planning_month.pack(side="left")
        planning_month.bind("<<ComboboxSelected>>", lambda _event: self.refresh_planning_projects())
        self.planning_action_var = tk.StringVar(value="Tous les services")
        ttk.Label(selector, text="Service :").pack(side="left", padx=(10, 3))
        planning_action = ttk.Combobox(selector, textvariable=self.planning_action_var, values=ACTIONS, state="readonly", width=21)
        planning_action.pack(side="left")
        planning_action.bind("<<ComboboxSelected>>", lambda _event: self.refresh_planning_projects())
        self.planning_client_label = ttk.Label(selector, text="Client : —")
        self.planning_client_label.pack(side="left", padx=12)

        entry = ttk.LabelFrame(self.planning_tab, text="Ajouter un livrable", padding=10)
        entry.pack(fill="x", pady=5)
        self.planning_name = tk.StringVar()
        self.planning_status = tk.StringVar(value="Pas encore soumis")
        self.planning_start = tk.StringVar()
        self.planning_end = tk.StringVar()
        self.edit_deliverable_id = None
        try:
            from tkcalendar import DateEntry
        except ImportError:
            DateEntry = None
        ttk.Label(entry, text="Livrable").grid(row=0, column=0, sticky="w", padx=5)
        ttk.Entry(entry, textvariable=self.planning_name, width=28).grid(row=1, column=0, padx=5)
        ttk.Label(entry, text="Status livrable").grid(row=0, column=1, sticky="w", padx=5)
        ttk.Combobox(entry, textvariable=self.planning_status, state="readonly", width=20,
                     values=("Soumis", "Pas encore soumis")).grid(row=1, column=1, padx=5)
        ttk.Label(entry, text="Date de démarrage (JJ/MM/AAAA)").grid(row=0, column=2, sticky="w", padx=5)
        if DateEntry:
            self.start_date_widget = DateEntry(entry, textvariable=self.planning_start, date_pattern="dd/mm/yyyy", width=16)
            self.start_date_widget.grid(row=1, column=2, padx=5)
            self.start_date_widget.bind("<Button-3>", lambda _event: (self.start_date_widget.drop_down(), "break")[1])
        else:
            ttk.Entry(entry, textvariable=self.planning_start, width=18).grid(row=1, column=2, padx=5)
        ttk.Label(entry, text="Date de fin (JJ/MM/AAAA)").grid(row=0, column=3, sticky="w", padx=5)
        if DateEntry:
            self.end_date_widget = DateEntry(entry, textvariable=self.planning_end, date_pattern="dd/mm/yyyy", width=16)
            self.end_date_widget.grid(row=1, column=3, padx=5)
            self.end_date_widget.bind("<Button-3>", lambda _event: (self.end_date_widget.drop_down(), "break")[1])
        else:
            ttk.Entry(entry, textvariable=self.planning_end, width=18).grid(row=1, column=3, padx=5)
        ttk.Button(entry, text="+ Ajouter", command=self.add_deliverable).grid(row=1, column=4, padx=10)
        ttk.Button(entry, text="Modifier", command=self.update_deliverable).grid(row=1, column=5, padx=5)
        ttk.Button(entry, text="Marquer soumis", command=self.mark_deliverable_submitted).grid(row=1, column=6, padx=5)

        columns = ("deliverable", "submitted", "start", "end", "status")
        self.planning_tree = ttk.Treeview(self.planning_tab, columns=columns, show="headings")
        headings = {"deliverable": "Livrable", "submitted": "Status livrable", "start": "Démarrage", "end": "Fin prévue", "status": "Status"}
        for col in columns:
            self.planning_tree.heading(col, text=headings[col])
            self.planning_tree.column(col, width=190 if col in ("deliverable", "status") else 135)
        self.planning_tree.pack(fill="both", expand=True, pady=(10, 0))
        self.planning_tree.bind("<<TreeviewSelect>>", self.select_deliverable)
        self.refresh_planning_projects()

    def refresh_planning_projects(self):
        if not hasattr(self, "planning_project_combo"):
            return
        month = month_number(self.planning_month_var.get())
        action = self.planning_action_var.get()
        with db_connection() as con:
            rows = con.execute("""SELECT i.reference,c.name,i.project_folder
                FROM invoices i JOIN clients c ON c.id=i.client_id
                WHERE (?='00' OR substr(i.created_at,6,2)=?)
                  AND (?='Tous les services' OR i.action_type=?) ORDER BY i.id DESC""", (month, month, action, action)).fetchall()
        self.planning_project_map = {f"{r['reference']} — {r['name']}": (r['reference'], r['project_folder'], r['name']) for r in rows}
        self.planning_project_combo["values"] = list(self.planning_project_map)
        if self.planning_project_map and self.planning_project_var.get() not in self.planning_project_map:
            self.planning_project_var.set(next(iter(self.planning_project_map)))
        self.refresh_planning()

    def refresh_planning(self):
        if not hasattr(self, "planning_tree"):
            return
        for item in self.planning_tree.get_children():
            self.planning_tree.delete(item)
        project = self.planning_project_map.get(self.planning_project_var.get()) if hasattr(self, "planning_project_map") else None
        if not project:
            self.planning_client_label.config(text="Client : —")
            return
        reference, _folder, client = project
        self.planning_client_label.config(text=f"Client : {client} | Projet : {reference}")
        with db_connection() as con:
            rows = con.execute("""SELECT d.* FROM deliverables d JOIN invoices i ON i.id=d.invoice_id
                WHERE i.reference=? ORDER BY d.id""", (reference,)).fetchall()
        today = date.today()
        for row in rows:
            status = self.deliverable_status(row, today)
            self.planning_tree.insert("", "end", iid=str(row["id"]), values=(row["name"], row["submitted"], date_to_french(row["start_date"]), date_to_french(row["end_date"]), status))

    @staticmethod
    def deliverable_status(row, today):
        if row["submitted"] == "Soumis":
            return f"Achevé — {date_to_french(row['completion_date']) or today.strftime('%d/%m/%Y')}"
        if not row["end_date"]:
            return "Date de fin non définie"
        try:
            end = date.fromisoformat(row["end_date"])
        except ValueError:
            return "Date invalide"
        days = (end - today).days
        return f"Temps restant {days} jours" if days >= 0 else f"Retard de {abs(days)} jours"

    def add_deliverable(self):
        project = self.planning_project_map.get(self.planning_project_var.get())
        name = self.planning_name.get().strip()
        if not project or not name:
            messagebox.showwarning("Planning", "Sélectionnez un projet et renseignez le livrable.")
            return
        try:
            start_date = date_to_iso(self.planning_start.get())
            end_date = date_to_iso(self.planning_end.get())
        except ValueError:
            messagebox.showerror("Planning", "Les dates doivent respecter le format JJ/MM/AAAA.")
            return
        reference = project[0]
        with db_connection() as con:
            invoice_id = con.execute("SELECT id FROM invoices WHERE reference=?", (reference,)).fetchone()[0]
            submitted = self.planning_status.get()
            completion = date.today().isoformat() if submitted == "Soumis" else None
            con.execute("""INSERT INTO deliverables(invoice_id,name,submitted,start_date,end_date,completion_date)
                VALUES(?,?,?,?,?,?)""", (invoice_id, name, submitted, start_date, end_date, completion))
        self.planning_name.set(""); self.planning_start.set(""); self.planning_end.set(""); self.planning_status.set("Pas encore soumis")
        self.edit_deliverable_id = None
        self.refresh_planning()

    def select_deliverable(self, _event=None):
        selected = self.planning_tree.selection()
        if not selected:
            return
        with db_connection() as con:
            row = con.execute("SELECT * FROM deliverables WHERE id=?", (selected[0],)).fetchone()
        if not row:
            return
        self.edit_deliverable_id = int(row["id"])
        self.planning_name.set(row["name"])
        self.planning_status.set(row["submitted"])
        self.planning_start.set(date_to_french(row["start_date"]))
        self.planning_end.set(date_to_french(row["end_date"]))

    def update_deliverable(self):
        if not self.edit_deliverable_id:
            messagebox.showwarning("Planning", "Sélectionnez d'abord un livrable à modifier.")
            return
        name = self.planning_name.get().strip()
        if not name:
            messagebox.showwarning("Planning", "Le nom du livrable est obligatoire.")
            return
        try:
            start_date = date_to_iso(self.planning_start.get())
            end_date = date_to_iso(self.planning_end.get())
        except ValueError:
            messagebox.showerror("Planning", "Les dates doivent respecter le format JJ/MM/AAAA.")
            return
        with db_connection() as con:
            old = con.execute("SELECT completion_date FROM deliverables WHERE id=?", (self.edit_deliverable_id,)).fetchone()
            completion = (old["completion_date"] if old else None)
            if self.planning_status.get() == "Soumis" and not completion:
                completion = date.today().isoformat()
            if self.planning_status.get() != "Soumis":
                completion = None
            con.execute("""UPDATE deliverables SET name=?,submitted=?,start_date=?,end_date=?,completion_date=?
                WHERE id=?""", (name, self.planning_status.get(), start_date, end_date, completion, self.edit_deliverable_id))
        self.refresh_planning()
        messagebox.showinfo("Planning", "Le livrable a été modifié.")

    def mark_deliverable_submitted(self):
        selected = self.planning_tree.selection()
        if not selected:
            messagebox.showwarning("Planning", "Sélectionnez un livrable dans le tableau.")
            return
        with db_connection() as con:
            con.execute("UPDATE deliverables SET submitted='Soumis', completion_date=? WHERE id=?",
                        (date.today().isoformat(), selected[0]))
        self.refresh_planning()

    def build_report_tab(self):
        ttk.Label(self.report_tab, text="Rapport comptable global", font=("Arial", 18, "bold")).pack(anchor="w")
        line = ttk.Frame(self.report_tab); line.pack(fill="x", pady=(12, 10))
        ttk.Label(line, text="Période :").pack(side="left")
        self.report_month_var = tk.StringVar(value="Tous les mois")
        report_month = ttk.Combobox(line, textvariable=self.report_month_var, values=MONTHS, state="readonly", width=18)
        report_month.pack(side="left", padx=8)
        self.report_action_var = tk.StringVar(value="Tous les services")
        ttk.Label(line, text="Service :").pack(side="left", padx=(10, 3))
        report_action = ttk.Combobox(line, textvariable=self.report_action_var, values=ACTIONS, state="readonly", width=21)
        report_action.pack(side="left")
        ttk.Button(line, text="Générer le rapport PDF", command=self.generate_report_pdf).pack(side="left", padx=8)
        self.report_summary = tk.StringVar(value="Sélectionnez une période puis générez le rapport PDF.")
        ttk.Label(self.report_tab, textvariable=self.report_summary, font=("Arial", 11), wraplength=850).pack(anchor="w", pady=12)
        ttk.Label(self.report_tab, text="Le PDF contient les tableaux financiers, les diagrammes et le pied de page administratif K3D Ingénierie et Conseils.").pack(anchor="w")

    def report_data(self):
        month = month_number(self.report_month_var.get())
        action = self.report_action_var.get()
        with db_connection() as con:
            rows = con.execute("""SELECT i.reference,i.created_at,i.lot,i.total,
                COALESCE(SUM(p.amount),0) paid,c.name client
                FROM invoices i JOIN clients c ON c.id=i.client_id
                LEFT JOIN payments p ON p.invoice_id=i.id
                WHERE (?='00' OR substr(i.created_at,6,2)=?)
                  AND (?='Tous les services' OR i.action_type=?)
                GROUP BY i.id ORDER BY i.created_at""", (month, month, action, action)).fetchall()
        return rows

    def generate_report_pdf(self):
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import mm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
            from reportlab.graphics.shapes import Drawing
            from reportlab.graphics.charts.barcharts import VerticalBarChart
            from reportlab.graphics.charts.legends import Legend
        except ImportError:
            messagebox.showerror("Dépendances manquantes", "Exécutez : python -m pip install -r requirements_comptabilite.txt")
            return
        rows = self.report_data()
        total = sum(float(r["total"]) for r in rows)
        paid = sum(float(r["paid"]) for r in rows)
        remaining = sum(max(0, float(r["total"]) - float(r["paid"])) for r in rows)
        self.report_summary.set(f"{len(rows)} projet(s) — Total : {money(total)} — Payé : {money(paid)} — À solder : {money(remaining)}")
        period = self.report_month_var.get().replace(" ", "_")
        filename = EXPORT_DIR / f"rapport_comptabilite_{date.today().year}_{period}.pdf"

        by_lot = {}
        for row in rows:
            lot = row["lot"] or "Non défini"
            item = by_lot.setdefault(lot, [0.0, 0.0])
            item[0] += float(row["total"]); item[1] += float(row["paid"])
        lots = list(by_lot) or ["Aucun projet"]
        chart = Drawing(490, 220)
        bar = VerticalBarChart(); bar.x = 55; bar.y = 45; bar.height = 145; bar.width = 390
        bar.data = [[by_lot[x][0] for x in lots], [by_lot[x][1] for x in lots]]
        bar.categoryAxis.categoryNames = lots; bar.categoryAxis.labels.angle = 20
        bar.categoryAxis.labels.fontSize = 8; bar.valueAxis.valueMin = 0
        bar.bars[0].fillColor = colors.HexColor("#2f5597"); bar.bars[1].fillColor = colors.HexColor("#70ad47")
        legend = Legend(); legend.x = 300; legend.y = 195; legend.colorNamePairs = [(colors.HexColor("#2f5597"), "Total"), (colors.HexColor("#70ad47"), "Payé")]
        chart.add(bar); chart.add(legend)

        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor("#1f4e79")))
        def footer(canvas, doc):
            canvas.saveState(); width, height = A4
            # Pied de page positionné dans la marge inférieure, avec la même
            # organisation que le modèle fourni.
            line_y = 38 * mm
            canvas.setStrokeColor(colors.HexColor("#1f1f1f")); canvas.line(18*mm, line_y, width-18*mm, line_y)
            canvas.setFillColor(colors.HexColor("#1f4e79")); canvas.setFont("Helvetica-Bold", 8.5)
            canvas.drawString(22*mm, 32*mm, "K3D Ingénierie et Conseils")
            canvas.setFont("Helvetica", 8); canvas.drawString(22*mm, 26*mm, "Parcelles Assainies U 20, Dakar")
            canvas.drawString(22*mm, 20*mm, "Tel: +221 76 671 71 72 |")
            canvas.drawString(22*mm, 14*mm, "Tel: +221 77 404 32")
            canvas.drawString(22*mm, 8*mm, "Email: k3dsenegal@gmail.com")
            canvas.setFont("Helvetica-Bold", 8.5); canvas.drawString(101*mm, 32*mm, "Gérant :")
            canvas.setFont("Helvetica", 8); canvas.drawString(101*mm, 26*mm, "Ibra Der")
            canvas.setFont("Helvetica-Bold", 8.5); canvas.drawString(137*mm, 32*mm, "Banque :")
            canvas.setFont("Helvetica", 8); canvas.drawString(137*mm, 26*mm, "Banque Islamique")
            canvas.drawString(137*mm, 20*mm, "N° Compte : 114970")
            canvas.setFont("Helvetica-Bold", 7.5); canvas.drawString(174*mm, 32*mm, "N° au registre de commerce")
            canvas.setFont("Helvetica", 7.5); canvas.drawString(174*mm, 26*mm, "RCCM : Sn Dkr 2023 A39827")
            canvas.drawString(174*mm, 20*mm, "NINEA : 0106240981r1")
            canvas.setFont("Helvetica", 12); canvas.drawRightString(width-20*mm, 19*mm, str(doc.page)); canvas.restoreState()

        doc = SimpleDocTemplate(str(filename), pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=42*mm)
        established = date.today().strftime('%d/%m/%Y')
        story = [Paragraph(f"RAPPORT DE COMPTABILITÉ GLOBALE — ÉTABLI LE {established}", styles["ReportTitle"]), Paragraph(f"Période : {self.report_month_var.get()} — Date d'établissement : {established}", styles["Normal"]), Spacer(1, 8*mm)]
        summary = [["Indicateur", "Montant"], ["Montant total des projets", money(total)], ["Montant total payé", money(paid)], ["Montant total à solder", money(remaining)]]
        table = Table(summary, colWidths=[95*mm, 65*mm]); table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4e79")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("GRID", (0,0), (-1,-1), .4, colors.grey), ("ALIGN", (1,1), (-1,-1), "RIGHT"), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold")]))
        story += [table, Spacer(1, 8*mm), Paragraph("Répartition par lot", styles["Heading2"]), chart, PageBreak(), Paragraph("Détail des projets", styles["Heading2"])]
        project_table = [["Référence", "Client", "Lot", "Total", "Payé", "À solder"]]
        for row in rows:
            t, p = float(row["total"]), float(row["paid"]); project_table.append([row["reference"], row["client"], row["lot"], money(t), money(p), money(max(0, t-p))])
        if len(project_table) == 1: project_table.append(["Aucun projet", "", "", money(0), money(0), money(0)])
        detail = Table(project_table, repeatRows=1, colWidths=[29*mm, 39*mm, 25*mm, 25*mm, 25*mm, 25*mm])
        detail.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4e79")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("GRID", (0,0), (-1,-1), .3, colors.grey), ("FONTSIZE", (0,0), (-1,-1), 7), ("ALIGN", (3,1), (-1,-1), "RIGHT")]))
        story.append(detail)
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        messagebox.showinfo("Rapport créé", f"Rapport PDF créé :\n{filename}")
        try: os.startfile(filename)
        except OSError: pass

    def render_phases(self):
        for child in self.phase.winfo_children():
            child.destroy()
        for c, (title, variable) in enumerate(self.phase_vars):
            ttk.Label(self.phase, text=title, font=("Arial", 10, "bold")).grid(row=0, column=c, padx=8)
            ttk.Entry(self.phase, textvariable=variable, width=20).grid(row=1, column=c, padx=8, pady=4)
            ttk.Label(self.phase, text="montant prévu (FCFA)").grid(row=2, column=c, padx=8)
            self.phase.columnconfigure(c, weight=1)
        ttk.Button(self.phase, text="+ Ajouter un paiement", command=self.add_payment_phase).grid(row=3, column=0, padx=8, pady=5)
        ttk.Button(self.phase, text="Calculer le solde", command=self.calculate_balance).grid(row=3, column=1, columnspan=max(1, len(self.phase_vars)-1), pady=5)

    def toggle_payment_fields(self):
        is_offer = self.action_type_var.get() == "Offre commerciale"
        self.phase.configure(text="Échéancier — paiements (désactivé pour une offre commerciale)" if is_offer else "Échéancier — paiements")
        for child in self.phase.winfo_children():
            if isinstance(child, (ttk.Entry, ttk.Button, ttk.Combobox)):
                child.configure(state="disabled" if is_offer else "normal")

    def add_payment_phase(self):
        # The new payment is inserted before the final Solde column.
        number = len(self.phase_vars)
        self.phase_vars.insert(-1, (f"Paiement {number}", tk.StringVar()))
        self.render_phases()

    def calculate_balance(self):
        try:
            total = float(self.vars["total"].get() or 0)
            a = float(self.vars["advance"].get() or 0)
            intermediate = sum(float(var.get() or 0) for _, var in self.phase_vars[1:-1])
            self.vars["balance"].set(str(max(0, total - a - intermediate)))
        except ValueError:
            messagebox.showerror("Erreur", "Les montants doivent être numériques.")

    def save(self):
        try:
            first_name = self.vars["first_name"].get().strip()
            last_name = self.vars["last_name"].get().strip()
            name = f"{first_name} {last_name}".strip()
            total = float(self.vars["total"].get())
            amounts = [(label, float(var.get() or 0)) for label, var in self.phase_vars]
            a = amounts[0][1]; b = amounts[-1][1]
            n = amounts[1][1] if len(amounts) > 2 else 0
            if not first_name or not last_name or total < 0 or any(amount < 0 for _, amount in amounts):
                raise ValueError
        except ValueError:
            messagebox.showerror("Erreur", "Veuillez renseigner le client et des montants valides.")
            return
        try:
            year = int(self.vars["year"].get() or date.today().year)
        except ValueError:
            messagebox.showerror("Erreur", "L'année doit être un nombre, par exemple 2026.")
            return
        action_type = self.action_type_var.get()
        if action_type == "Offre commerciale":
            if self.client_id:
                messagebox.showwarning("Offre commerciale", "Pour créer une offre, videz le formulaire puis sélectionnez Offre commerciale.")
                return
            offer_folder = EXPORT_DIR / "Offres_commerciales"
            offer_folder.mkdir(parents=True, exist_ok=True)
            offer_reference = self._new_offer_reference(year)
            offer_file = self.create_offer_pdf(
                offer_folder, offer_reference, first_name, last_name,
                self.vars["address"].get(), self.vars["phone"].get(),
                self.vars["email"].get(), self.vars["description"].get(),
                self.vars["lot"].get() or "Les deux", total, year,
            )
            self.clear_form()
            messagebox.showinfo("Offre commerciale créée", f"Offre PDF créée :\n{offer_file}")
            return
        editing_id = self.client_id  # invoice id when an existing project is selected
        if editing_id:
            with db_connection() as con:
                existing = con.execute("SELECT reference,project_folder,client_id FROM invoices WHERE id=?", (editing_id,)).fetchone()
            if not existing:
                editing_id = None
        ref = existing["reference"] if editing_id else self._new_reference(year)
        lot = self.vars["lot"].get() or "Les deux"
        search_text = " ".join([name, first_name, last_name, self.vars["address"].get(), self.vars["phone"].get(), self.vars["email"].get(), ref, str(year), lot, self.vars["description"].get()]).lower()
        if editing_id:
            project_folder = Path(existing["project_folder"])
        else:
            folder_name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{ref}_{name}").strip("_")
            project_folder = EXPORT_DIR / folder_name
        project_folder.mkdir(parents=True, exist_ok=True)
        for subfolder in (
            "01- Gestion-contrat",
            "02- Données du client",
            "03- Conception",
            "04- Livrables",
            "05- Dossier final",
        ):
            (project_folder / subfolder).mkdir(exist_ok=True)
        with db_connection() as con:
            if editing_id:
                cid = existing["client_id"]
                con.execute("UPDATE clients SET name=?,first_name=?,last_name=?,address=?,phone=?,email=? WHERE id=?",
                            (name, first_name, last_name, self.vars["address"].get(), self.vars["phone"].get(), self.vars["email"].get(), cid))
                con.execute("""UPDATE invoices SET description=?,total=?,advance=?,next_payment=?,balance=?,
                    year=?,search_text=?,lot=?,action_type=? WHERE id=?""",
                            (self.vars["description"].get(), total, a, n, b, year, search_text, lot, action_type, editing_id))
                iid = editing_id
                # Remplacer l'échéancier du même projet, sans créer un nouveau projet.
                con.execute("DELETE FROM payments WHERE invoice_id=?", (iid,))
            else:
                cur = con.execute("INSERT INTO clients(name,first_name,last_name,address,phone,email) VALUES(?,?,?,?,?,?)",
                                  (name, first_name, last_name, self.vars["address"].get(), self.vars["phone"].get(), self.vars["email"].get()))
                cid = cur.lastrowid
                con.execute("INSERT INTO invoices(client_id,reference,description,total,advance,next_payment,balance,year,search_text,lot,project_folder,action_type,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (cid, ref, self.vars["description"].get(), total, a, n, b, year, search_text, lot, str(project_folder), action_type, date.today().isoformat()))
                iid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            for phase, amount in amounts:
                if amount:
                    con.execute("INSERT INTO payments(invoice_id,phase,amount,paid_at) VALUES(?,?,?,?)", (iid, phase, amount, date.today().isoformat()))
        self.create_contract_docx(project_folder, ref, first_name, last_name,
                                  self.vars["address"].get(), self.vars["phone"].get(),
                                  self.vars["email"].get(), self.vars["description"].get(),
                                  lot, total, amounts, year)
        if not editing_id and hasattr(self, "search_var"):
            self.search_var.set("")
        self.refresh_list()
        # Sélectionner immédiatement le projet dans l'onglet Comptabilité et
        # synchroniser les onglets Documents client / Planning.
        if str(iid) in self.tree.get_children():
            self.tree.selection_set(str(iid))
            self.tree.focus(str(iid))
            self.tree.see(str(iid))
            self.select_invoice()
        self.clear_form()
        action = "mis à jour" if editing_id else "créé"
        messagebox.showinfo(
            "Projet validé",
            f"Le projet {ref} a été {action}.\n\n"
            f"Dossier créé/sélectionné :\n{project_folder}\n\n"
            "Les onglets Documents client, Gestion et Planning ont été actualisés."
        )

    def delete_selected_project(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Suppression", "Sélectionnez d'abord un projet dans le tableau.")
            return
        invoice_id = int(selected[0])
        with db_connection() as con:
            row = con.execute("""SELECT i.reference,i.project_folder,c.name,i.client_id
                FROM invoices i JOIN clients c ON c.id=i.client_id WHERE i.id=?""", (invoice_id,)).fetchone()
        if not row:
            messagebox.showerror("Suppression", "Projet introuvable.")
            return
        confirmation = messagebox.askyesno(
            "Confirmer la suppression",
            f"Supprimer définitivement le projet {row['reference']} ({row['name']}) ?\n\n"
            "Cette action supprimera aussi les paiements, les livrables, le contrat, "
            "les documents et tous les sous-dossiers du projet."
        )
        if not confirmation:
            return
        with db_connection() as con:
            con.execute("DELETE FROM deliverables WHERE invoice_id=?", (invoice_id,))
            con.execute("DELETE FROM payments WHERE invoice_id=?", (invoice_id,))
            con.execute("DELETE FROM invoices WHERE id=?", (invoice_id,))
            # Chaque prestation crée actuellement son propre client. On ne
            # supprime le client que s'il n'est plus lié à aucun projet.
            still_used = con.execute("SELECT 1 FROM invoices WHERE client_id=? LIMIT 1", (row["client_id"],)).fetchone()
            if not still_used:
                con.execute("DELETE FROM clients WHERE id=?", (row["client_id"],))
        project_folder = Path(row["project_folder"]) if row["project_folder"] else None
        if project_folder and project_folder.exists():
            try:
                # Protection contre une suppression accidentelle hors du
                # répertoire des projets de l'application.
                if EXPORT_DIR.resolve() in project_folder.resolve().parents:
                    shutil.rmtree(project_folder)
                else:
                    messagebox.showwarning("Dossier non supprimé", f"Le dossier est hors de documents_k3d :\n{project_folder}")
            except OSError as error:
                messagebox.showwarning("Dossier non supprimé", f"La base a été supprimée, mais le dossier n'a pas pu être supprimé :\n{error}")
        self.clear_form()
        self.refresh_list()
        messagebox.showinfo("Suppression", "Le projet et ses composants ont été supprimés.")

    def _new_offer_reference(self, year):
        folder = EXPORT_DIR / "Offres_commerciales"
        number = 1
        while (folder / f"Offre_Commerciale_K3D_{year}_{number:03d}.pdf").exists():
            number += 1
        return f"OFFRE-K3D-{year}-{number:03d}"

    def create_offer_pdf(self, folder, reference, first_name, last_name,
                         address, phone, email, description, lot, total, year):
        """Generate the commercial-offer PDF in the shared offers folder."""
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.pdfgen import canvas
        except ImportError:
            messagebox.showerror("Dépendances manquantes", "Exécutez : python -m pip install -r requirements_comptabilite.txt")
            return ""
        filename = folder / f"Offre_Commerciale_K3D_{year}_{reference.split('-')[-1]}.pdf"
        c = canvas.Canvas(str(filename), pagesize=A4); width, height = A4
        # En-tête proche du modèle Word fourni.
        c.setFillColor(colors.HexColor("#1A365D")); c.setFont("Helvetica-Bold", 19)
        c.drawString(22*mm, height-25*mm, "K3D Ingénierie et Conseils")
        c.setFillColor(colors.black); c.setFont("Helvetica-Bold", 15)
        c.drawCentredString(width/2, height-45*mm, "PROPOSITION TECHNIQUE ET FINANCIÈRE")
        c.setFont("Helvetica", 10); c.drawCentredString(width/2, height-52*mm, "Offre commerciale — Études, architecture et construction")
        y = height - 72*mm
        c.setFillColor(colors.HexColor("#F7FAFC")); c.rect(20*mm, y-30*mm, width-40*mm, 30*mm, fill=1, stroke=0)
        c.setFillColor(colors.black); c.setFont("Helvetica-Bold", 10)
        c.drawString(25*mm, y-8*mm, f"DESTINATAIRE : {first_name} {last_name}")
        c.drawString(25*mm, y-16*mm, f"RÉFÉRENCE : {reference}")
        c.drawString(25*mm, y-24*mm, f"DATE D'ÉTABLISSEMENT : {date.today().strftime('%d/%m/%Y')} — ANNÉE : {year}")
        c.setFont("Helvetica", 9); c.drawRightString(width-25*mm, y-8*mm, f"Téléphone : {phone or '—'}")
        c.drawRightString(width-25*mm, y-16*mm, f"Email : {email or '—'}")
        c.drawRightString(width-25*mm, y-24*mm, f"Adresse : {address or '—'}")
        y -= 45*mm; c.setFillColor(colors.HexColor("#1A365D")); c.setFont("Helvetica-Bold", 12)
        c.drawString(22*mm, y, "1. Contexte et description du projet")
        c.setFillColor(colors.black); c.setFont("Helvetica", 10)
        text = c.beginText(25*mm, y-9*mm); text.setLeading(15)
        for line in (description or "Description de la mission à préciser.").splitlines() or ["Description de la mission à préciser."]:
            text.textLine(line)
        c.drawText(text)
        y -= 38*mm; c.setFillColor(colors.HexColor("#1A365D")); c.setFont("Helvetica-Bold", 12)
        c.drawString(22*mm, y, "2. Périmètre de la mission de K3D")
        c.setFillColor(colors.black); c.setFont("Helvetica", 10)
        c.drawString(25*mm, y-9*mm, f"Lot concerné : {lot}")
        c.drawString(25*mm, y-17*mm, "La mission sera précisée et validée avec le Client avant démarrage.")
        y -= 35*mm; c.setFillColor(colors.HexColor("#1A365D")); c.setFont("Helvetica-Bold", 12)
        c.drawString(22*mm, y, "3. Proposition financière")
        y -= 10*mm; c.setFillColor(colors.HexColor("#1A365D")); c.rect(22*mm, y-9*mm, width-44*mm, 9*mm, fill=1, stroke=0)
        c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 9); c.drawString(27*mm, y-6*mm, "Désignation des prestations")
        c.drawRightString(width-27*mm, y-6*mm, "Montant total (FCFA)")
        y -= 19*mm; c.setFillColor(colors.black); c.setFont("Helvetica", 10)
        c.rect(22*mm, y-12*mm, width-44*mm, 12*mm, fill=0, stroke=1)
        c.drawString(27*mm, y-8*mm, f"Prestation — lot {lot}")
        c.drawRightString(width-27*mm, y-8*mm, money(total))
        y -= 25*mm; c.setFont("Helvetica-Bold", 11); c.drawRightString(width-27*mm, y, f"MONTANT TOTAL HT : {money(total)}")
        c.setFont("Helvetica", 9); c.drawString(22*mm, 52*mm, "Pour l'Entreprise K3D — Gérant : Ibra DER")
        c.drawString(width/2+10*mm, 52*mm, "Pour le Client — Bon pour accord")
        c.line(22*mm, 30*mm, 82*mm, 30*mm); c.line(width/2+10*mm, 30*mm, width-22*mm, 30*mm)
        c.setFont("Helvetica", 8); c.drawString(22*mm, 20*mm, "K3D Ingénierie et Conseils — Offre commerciale")
        c.drawRightString(width-22*mm, 20*mm, f"Date d'établissement : {date.today().strftime('%d/%m/%Y')}")
        c.save()
        return filename

    def _next_number(self):
        with db_connection() as con:
            return con.execute("SELECT COUNT(*) + 1 FROM invoices").fetchone()[0]

    def _new_reference(self, year):
        """Return a reference that is unique, even after deletions or edits."""
        number = 1
        with db_connection() as con:
            while True:
                reference = f"K3D-{year}-{number:03d}"
                exists = con.execute("SELECT 1 FROM invoices WHERE reference=?", (reference,)).fetchone()
                if not exists:
                    return reference
                number += 1

    def refresh_list(self):
        for item in self.tree.get_children(): self.tree.delete(item)
        search = f"%{self.search_var.get().strip().lower()}%" if hasattr(self, "search_var") else "%"
        month = month_number(self.month_var.get()) if hasattr(self, "month_var") else "00"
        action = self.action_filter_var.get() if hasattr(self, "action_filter_var") else "Tous les services"
        with db_connection() as con:
            rows = con.execute("""SELECT i.id,i.reference,c.name,i.total,i.lot,
                COALESCE(SUM(p.amount),0) paid,i.total-COALESCE(SUM(p.amount),0) remaining,i.created_at
                FROM invoices i JOIN clients c ON c.id=i.client_id LEFT JOIN payments p ON p.invoice_id=i.id
                WHERE (i.search_text LIKE ? OR c.name LIKE ? OR c.email LIKE ? OR c.phone LIKE ?)
                  AND (?='00' OR substr(i.created_at,6,2)=?)
                  AND (?='Tous les services' OR i.action_type=?)
                GROUP BY i.id ORDER BY i.id DESC""", (search, search, search, search, month, month, action, action)).fetchall()
        for r in rows:
            self.tree.insert("", "end", iid=str(r["id"]), values=(r["id"], r["reference"], r["name"], r["lot"], money(r["total"]), money(r["paid"]), money(r["remaining"]), r["created_at"]))
        if hasattr(self, "project_combo"):
            self.refresh_upload_projects()
        if hasattr(self, "management_tree"):
            self.refresh_management()
        if hasattr(self, "planning_project_combo"):
            self.refresh_planning_projects()

    def select_invoice(self, _event=None):
        selected = self.tree.selection()
        if not selected: return
        with db_connection() as con:
            r = con.execute("SELECT i.*,c.* FROM invoices i JOIN clients c ON c.id=i.client_id WHERE i.id=?", (selected[0],)).fetchone()
            payment_rows = con.execute("SELECT phase,amount FROM payments WHERE invoice_id=? ORDER BY id", (selected[0],)).fetchall()
        for key in self.vars:
            if key in r.keys(): self.vars[key].set(r[key] or "")
        self.action_type_var.set(r["action_type"] or "Projet étude")
        self.toggle_payment_fields()
        self.phase_vars = []
        middle_index = 0
        for payment in payment_rows:
            phase = payment["phase"]
            if phase == "Avance":
                variable = self.vars["advance"]
            elif phase == "Solde":
                variable = self.vars["balance"]
            else:
                middle_index += 1
                variable = self.vars["next_payment"] if middle_index == 1 else tk.StringVar()
            variable.set(str(payment["amount"]))
            self.phase_vars.append(("Paiement 2" if phase == "Paiement suivant" else phase, variable))
        if not any(label == "Avance" for label, _ in self.phase_vars):
            self.phase_vars.insert(0, ("Avance", self.vars["advance"]))
        if not any(label == "Solde" for label, _ in self.phase_vars):
            self.phase_vars.append(("Solde", self.vars["balance"]))
        if len(self.phase_vars) < 3:
            self.phase_vars.insert(-1, ("Paiement 2", self.vars["next_payment"]))
        self.render_phases()
        self.client_id = int(selected[0])
        # La ligne sélectionnée devient automatiquement le projet actif dans
        # l'onglet Documents client.
        self.refresh_upload_projects()
        for label, folder in self.project_map.items():
            if r["reference"] in label:
                self.project_var.set(label)
                self.show_project_documents()
                break
        if hasattr(self, "planning_project_map"):
            for label in self.planning_project_map:
                if r["reference"] in label:
                    self.planning_project_var.set(label)
                    self.refresh_planning()
                    break

    def clear_form(self):
        self.client_id = None
        for var in self.vars.values(): var.set("")
        self.vars["year"].set(str(date.today().year))
        self.action_type_var.set("Projet étude")
        self.phase_vars = [("Avance", self.vars["advance"]), ("Paiement 2", self.vars["next_payment"]), ("Solde", self.vars["balance"])]
        self.render_phases()
        self.toggle_payment_fields()

    def settings(self):
        with db_connection() as con: current = con.execute("SELECT value FROM settings WHERE key='website'").fetchone()[0]
        win = tk.Toplevel(self); win.title("Paramètres"); win.grab_set()
        ttk.Label(win, text="Lien du site web (utilisé dans le QR code)").pack(padx=15, pady=(15, 4))
        value = tk.StringVar(value=current)
        ttk.Entry(win, textvariable=value, width=55).pack(padx=15, pady=4)
        def save_setting():
            with db_connection() as con: con.execute("UPDATE settings SET value=? WHERE key='website'", (value.get().strip(),))
            win.destroy()
        ttk.Button(win, text="Enregistrer", command=save_setting).pack(pady=12)

    def make_pdf(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Sélection", "Sélectionnez une prestation dans la liste.")
            return
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib import colors
            from reportlab.lib.units import mm
            from reportlab.pdfgen import canvas
            import qrcode
        except ImportError:
            messagebox.showerror("Dépendances manquantes", "Exécutez : python -m pip install -r requirements_comptabilite.txt")
            return
        iid = selected[0]
        with db_connection() as con:
            r = con.execute("SELECT i.*,c.name client,c.first_name,c.last_name,c.address,c.phone,c.email FROM invoices i JOIN clients c ON c.id=i.client_id WHERE i.id=?", (iid,)).fetchone()
            website = con.execute("SELECT value FROM settings WHERE key='website'").fetchone()[0]
            pays = con.execute("SELECT phase,amount,paid_at FROM payments WHERE invoice_id=?", (iid,)).fetchall()
        project_folder = Path(r["project_folder"]) if r["project_folder"] else EXPORT_DIR / r["reference"]
        project_folder.mkdir(parents=True, exist_ok=True)
        filename = project_folder / f"{r['reference']}.pdf"
        c = canvas.Canvas(str(filename), pagesize=A4); w, h = A4
        # Logo K3D réduit et entièrement contenu dans l'en-tête de la facture.
        ox, oy = 20 * mm, h - 43 * mm
        path = c.beginPath()
        path.moveTo(ox, oy); path.lineTo(ox + 8 * mm, oy + 6 * mm)
        path.lineTo(ox + 8 * mm, oy + 20 * mm); path.lineTo(ox, oy + 20 * mm)
        path.close()
        c.setFillColor(colors.HexColor("#ff5128")); c.drawPath(path, fill=1, stroke=0)
        path = c.beginPath()
        path.moveTo(ox + 8 * mm, oy + 6 * mm); path.lineTo(ox + 20 * mm, oy + 20 * mm)
        path.lineTo(ox + 34 * mm, oy + 20 * mm); path.lineTo(ox + 20 * mm, oy + 6 * mm)
        path.lineTo(ox + 34 * mm, oy - 7 * mm); path.lineTo(ox + 20 * mm, oy - 7 * mm)
        path.close()
        c.setFillColor(colors.HexColor("#999999")); c.drawPath(path, fill=1, stroke=0)
        c.setFont("Helvetica-Bold", 13); c.setFillColor(colors.HexColor("#4a4a4a")); c.drawString(20 * mm, h - 50 * mm, "K3D")
        c.setFont("Helvetica", 7); c.setFillColor(colors.HexColor("#999999")); c.drawString(34 * mm, h - 50 * mm, "SÉNÉGAL")
        c.setFillColor(colors.black); c.setFont("Helvetica-Bold", 18); c.drawRightString(w-20*mm, h-25*mm, "PRESTATION")
        c.setFont("Helvetica", 10); c.drawRightString(w-20*mm, h-32*mm, f"Référence : {r['reference']}  |  Année : {r['year']}")
        y = h-70*mm; c.setFont("Helvetica-Bold", 11); c.drawString(20*mm, y, "CLIENT")
        c.setFont("Helvetica", 10); y -= 6*mm
        for text in (f"Prénom : {r['first_name']}", f"Nom : {r['last_name']}", r["address"], r["phone"], r["email"], f"Lot : {r['lot']}"):
            if text: c.drawString(20*mm, y, str(text)); y -= 5*mm
        y -= 5*mm; c.setFont("Helvetica-Bold", 11); c.drawString(20*mm, y, "DÉTAIL DE LA PRESTATION")
        y -= 8*mm; c.setFillColor(colors.HexColor("#eeeeee")); c.rect(20*mm, y-8*mm, w-40*mm, 10*mm, fill=1, stroke=0)
        c.setFillColor(colors.black); c.setFont("Helvetica-Bold", 10); c.drawString(24*mm, y-4*mm, "Description"); c.drawRightString(w-24*mm, y-4*mm, "Montant")
        y -= 16*mm; c.setFont("Helvetica", 10); c.drawString(24*mm, y, r["description"] or "Prestation K3D Sénégal"); c.drawRightString(w-24*mm, y, money(r["total"]))
        y -= 14*mm; c.setFont("Helvetica-Bold", 11); c.drawString(20*mm, y, "ÉCHÉANCIER")
        y -= 8*mm; c.setFont("Helvetica-Bold", 10); c.drawString(24*mm, y, "Phase"); c.drawRightString(w-24*mm, y, "Montant prévu")
        y -= 6*mm; c.setFont("Helvetica", 10)
        # Les phases enregistrées dans payments incluent les paiements ajoutés avec +.
        displayed = pays or [{"phase": "Avance", "amount": r["advance"]}, {"phase": "Solde", "amount": r["balance"]}]
        for payment in displayed:
            c.drawString(24*mm, y, str(payment["phase"])); c.drawRightString(w-24*mm, y, money(payment["amount"])); y -= 6*mm
        y -= 6*mm; c.setFont("Helvetica-Bold", 11); c.drawString(20*mm, y, "TOTAL : " + money(r["total"]))
        c.setFont("Helvetica", 10); c.drawRightString(w-20*mm, y, "Date : " + r["created_at"])
        # QR code du site, si renseigné.
        if website:
            qr_path = project_folder / f"{r['reference']}_qr.png"; qrcode.make(website).save(qr_path); c.drawImage(str(qr_path), w-53*mm, 25*mm, 30*mm, 30*mm, preserveAspectRatio=True)
            c.setFont("Helvetica", 8); c.drawCentredString(w-38*mm, 22*mm, "Visitez notre site")
        c.setFont("Helvetica", 10); c.drawString(25*mm, 43*mm, "Signature du client :"); c.line(25*mm, 25*mm, 90*mm, 25*mm)
        c.drawString(w-85*mm, 43*mm, "Signature K3D Sénégal :"); c.line(w-85*mm, 25*mm, w-20*mm, 25*mm)
        c.save(); messagebox.showinfo("PDF créé", f"Document enregistré dans :\n{filename}")
        try: os.startfile(filename)
        except OSError: pass


if __name__ == "__main__":
    gate = LicenseGate()
    gate.mainloop()
    if gate.license_ok:
        init_database()
        App().mainloop()
