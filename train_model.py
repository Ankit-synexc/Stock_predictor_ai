"""
train_model.py
==============
Retrain the stock direction prediction model using the full AAPL daily CSV.

Fixes applied vs the original notebook (test.ipynb):
  - Uses RandomForestClassifier (not Regressor) on a binary classification target
  - Trains on 11K daily bars (1980-2026), not 80 intraday 5-min bars
  - Evaluates with classification metrics: Accuracy, F1, AUC-ROC
  - Temporal (chronological) train/test split — no look-ahead bias
  - Exports model, pipeline, and updated metadata to ML_models/

Run:
    .venv\\Scripts\\python.exe train_model.py
    (from inside Stock_predictor/)
"""

import os
import json
from datetime import datetime

import numpy as np
import pandas as pd
import joblib

from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score, classification_report
)

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
CSV_PATH  = os.path.join(BASE_DIR, "Data", "AAPL_indicators_1980-12-12_to_2026-05-18.csv")
MODEL_DIR = os.path.join(BASE_DIR, "ML_models")

# ── Features (same 15 as current metadata — keeps API contract identical) ──────
FEATURES = [
    "Open", "High", "Low", "Close", "Volume",
    "SMA_5", "SMA_10", "SMA_20", "SMA_50",
    "MACD", "MACD_Signal",
    "RSI",
    "BB_Upper", "BB_Lower",
    "Volume_MA_20",
]
TARGET = "Target_Class"   # 1 = next day UP, 0 = next day DOWN

# ── 1. Load ────────────────────────────────────────────────────────────────────
print("[1/5] Loading CSV …")
df = pd.read_csv(CSV_PATH, parse_dates=["Date"])
df = df.sort_values("Date").reset_index(drop=True)
print(f"      Loaded {len(df):,} rows, {df.shape[1]} columns.")
print(f"      Date range: {df['Date'].min().date()} -> {df['Date'].max().date()}")

# Drop any rows with NaN in the features or target we need
df_clean = df.dropna(subset=FEATURES + [TARGET]).copy()
print(f"      After dropna: {len(df_clean):,} rows kept.")

# ── 2. Build X / y ─────────────────────────────────────────────────────────────
X = df_clean[FEATURES].copy()
y = df_clean[TARGET].astype(int).copy()

print(f"\n      Target distribution:\n{y.value_counts().rename({0:'DOWN(0)', 1:'UP(1)'}).to_string()}")

# ── 3. Temporal split ──────────────────────────────────────────────────────────
print("\n[2/5] Splitting data (temporal 80 / 20) …")
split_idx = int(len(X) * 0.80)
X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

train_dates = df_clean["Date"].iloc[:split_idx]
test_dates  = df_clean["Date"].iloc[split_idx:]
print(f"      Train: {len(X_train):,} rows  ({train_dates.min().date()} -> {train_dates.max().date()})")
print(f"      Test : {len(X_test):,} rows  ({test_dates.min().date()} -> {test_dates.max().date()})")

# ── 4. Pipeline ────────────────────────────────────────────────────────────────
print("\n[3/5] Fitting feature pipeline (imputer + scaler) …")
feature_pipeline = Pipeline([
    ("imputer", SimpleImputer(strategy="median")),
    ("scaler",  StandardScaler()),
])
X_train_proc = feature_pipeline.fit_transform(X_train)
X_test_proc  = feature_pipeline.transform(X_test)

# ── 5. Train ───────────────────────────────────────────────────────────────────
print("\n[4/5] Training RandomForestClassifier …")
model = RandomForestClassifier(
    n_estimators=300,
    max_depth=12,
    min_samples_leaf=20,      # prevents overfitting on 11K rows
    max_features="sqrt",
    n_jobs=-1,
    random_state=42,
    class_weight="balanced",  # handles any class imbalance
)
model.fit(X_train_proc, y_train)

# ── 6. Evaluate ────────────────────────────────────────────────────────────────
y_pred = model.predict(X_test_proc)
y_prob = model.predict_proba(X_test_proc)[:, 1]

acc = accuracy_score(y_test, y_pred)
f1  = f1_score(y_test, y_pred, zero_division=0)
auc = roc_auc_score(y_test, y_prob)

print("\n      -- Classification Report --------------------")
print(classification_report(y_test, y_pred,
                            target_names=["DOWN (0)", "UP (1)"],
                            zero_division=0))
print(f"      Accuracy : {acc:.4f}")
print(f"      F1 Score : {f1:.4f}  (UP class)")
print(f"      AUC-ROC  : {auc:.4f}")

# Top-5 feature importances
feat_imp = pd.Series(model.feature_importances_, index=FEATURES).sort_values(ascending=False)
print("\n      Top-5 features by importance:")
print(feat_imp.head(5).to_string())

# ── 7. Export ──────────────────────────────────────────────────────────────────
print("\n[5/5] Exporting model, pipeline, and metadata …")

joblib.dump(model,            os.path.join(MODEL_DIR, "Apple_trading_model.joblib"))
joblib.dump(feature_pipeline, os.path.join(MODEL_DIR, "Apple_trading_pipeline.joblib"))

metadata = {
    "training_date":  datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    "target_asset":   "AAPL",
    "features_used":  FEATURES,
    "algorithm":      "RandomForestClassifier",
    "best_model":     "RandomForestClassifier",
    "interval":       "1day",
    "period":         f"{df_clean['Date'].min().date()} to {df_clean['Date'].max().date()}",
    "train_rows":     int(len(X_train)),
    "test_rows":      int(len(X_test)),
    "test_accuracy":  round(float(acc), 4),
    "test_f1":        round(float(f1),  4),
    "test_auc":       round(float(auc), 4),
}

with open(os.path.join(MODEL_DIR, "model_metadata.json"), "w") as f:
    json.dump(metadata, f, indent=4)

print("      ✅ Done -- files written to ML_models/")

print("         Apple_trading_model.joblib")
print("         Apple_trading_pipeline.joblib")
print("         model_metadata.json")
print(f"\n      Test Accuracy={acc:.4f}  F1={f1:.4f}  AUC={auc:.4f}")
