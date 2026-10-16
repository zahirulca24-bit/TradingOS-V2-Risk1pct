import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { apiUrl, setAuthToken } from "./api";
import "./LoginPage.css";

export default function LoginPage() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleLogin(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const response = await fetch(apiUrl("/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ password }),
      });

      if (!response.ok) {
        if (response.status === 401) {
          throw new Error("Incorrect password");
        }
        throw new Error("Login failed. Check backend connection.");
      }

      const data = await response.json().catch(() => ({}));
      if (data && data.token) {
        setAuthToken(data.token);
      }

      setPassword("");
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="tos-login-screen">
      <form className="tos-login-card" onSubmit={handleLogin}>
        <div className="tos-login-logo">T</div>
        <h1>TradingOS V2</h1>
        <p>Sign in to your trading intelligence workspace</p>

        <label htmlFor="tos-password">Password</label>
        <input
          id="tos-password"
          type={showPassword ? "text" : "password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Enter your password"
          autoComplete="current-password"
          required
        />

        <label className="tos-show-password">
          <input
            type="checkbox"
            checked={showPassword}
            onChange={(e) => setShowPassword(e.target.checked)}
          />
          Show password
        </label>

        {error && <p role="alert" style={{ color: "#f87171" }}>{error}</p>}

        <button type="submit" disabled={loading}>
          {loading ? "Signing In..." : "Sign In"}
        </button>

        <small>READ ONLY · NO ORDER EXECUTION</small>
      </form>
    </main>
  );
}
