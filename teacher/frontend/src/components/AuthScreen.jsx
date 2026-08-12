import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  EnvelopeSimple,
  Eye,
  EyeSlash,
  Globe,
  LockKey,
  UserCircle,
} from "@phosphor-icons/react";
import {
  confirmEmail,
  confirmPasswordReset,
  exchangeCrmSso,
  getAuthCapabilities,
  loginTeacher,
  registerTeacher,
  requestPasswordReset,
  resendVerification,
} from "../api/auth-api";
import { localizeApiError } from "../api-error-copy";
import { publicAsset } from "../public-assets";

const copy = (language, english, chinese) => language === "zh" ? chinese : english;

const initialFields = {
  teacherId: "",
  email: "",
  password: "",
  confirmPassword: "",
};

function PasswordField({ id, label, value, onChange, language, autoComplete = "current-password" }) {
  const [visible, setVisible] = useState(false);
  return (
    <label className="auth-field" htmlFor={id}>
      <span>{label}</span>
      <span className="auth-input-shell">
        <LockKey size={19} />
        <input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={onChange}
          placeholder={copy(language, "At least 12 characters", "至少 12 位")}
          autoComplete={autoComplete}
        />
        <button
          className="auth-visibility-button"
          type="button"
          onClick={() => setVisible((current) => !current)}
          aria-label={copy(language, visible ? "Hide password" : "Show password", visible ? "隐藏密码" : "显示密码")}
        >
          {visible ? <EyeSlash size={19} /> : <Eye size={19} />}
        </button>
      </span>
    </label>
  );
}

export default function AuthScreen({ language, onLanguageChange, onAuthenticated }) {
  const [mode, setMode] = useState("login");
  const [fields, setFields] = useState(initialFields);
  const [linkToken, setLinkToken] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [capabilities, setCapabilities] = useState(null);

  const updateField = (field) => (event) => {
    setFields((current) => ({ ...current, [field]: event.target.value }));
    setError("");
  };

  const switchMode = (nextMode) => {
    setMode(nextMode);
    setError("");
    setNotice("");
    setFields((current) => ({ ...current, password: "", confirmPassword: "" }));
  };

  const runRequest = async (request, onSuccess) => {
    setSubmitting(true);
    try {
      const result = await request();
      onSuccess(result);
    } catch (requestError) {
      setError(localizeApiError(
        requestError,
        language,
        copy(language, "The request failed. Please try again.", "请求失败，请稍后重试。"),
      ));
    } finally {
      setSubmitting(false);
    }
  };

  const validEmail = fields.email.trim().includes("@") && fields.email.trim().includes(".");

  const submitLogin = (event) => {
    event.preventDefault();
    if (!validEmail) {
      setError(copy(language, "Enter a valid email address.", "请输入有效的邮箱地址。"));
      return;
    }
    if (!fields.password) {
      setError(copy(language, "Enter your password.", "请输入密码。"));
      return;
    }
    runRequest(
      () => loginTeacher({ email: fields.email.trim(), password: fields.password }),
      onAuthenticated,
    );
  };

  const submitRegister = (event) => {
    event.preventDefault();
    if (!fields.teacherId.trim()) {
      setError(copy(language, "Enter your teacher ID.", "请输入教师编号。"));
      return;
    }
    if (!validEmail) {
      setError(copy(language, "Enter a valid email address.", "请输入有效的邮箱地址。"));
      return;
    }
    if (fields.password.length < 12) {
      setError(copy(language, "Password must contain at least 12 characters.", "密码至少需要 12 位。"));
      return;
    }
    if (fields.password !== fields.confirmPassword) {
      setError(copy(language, "Passwords do not match.", "两次输入的密码不一致。"));
      return;
    }
    runRequest(
      () => registerTeacher({
        email: fields.email.trim(),
        teacherId: fields.teacherId.trim(),
        password: fields.password,
      }),
      (result) => {
        setMode("verify-pending");
        setNotice(result.verificationEmailSent
          ? copy(language, "Open the verification link sent to your email.", "请打开邮件中的验证链接。")
          : copy(language, "Your account was created, but email delivery is not configured. Please contact support.", "账号已创建，但邮件服务尚未配置，请联系支持人员。"));
      },
    );
  };

  const submitForgot = (event) => {
    event.preventDefault();
    if (!validEmail) {
      setError(copy(language, "Enter the email used for your account.", "请输入注册账户时使用的邮箱。"));
      return;
    }
    runRequest(
      () => requestPasswordReset(fields.email.trim()),
      () => {
        setMode("reset-pending");
        setNotice(copy(language, "If the account exists, a password reset link has been sent.", "如果账户存在，密码重置链接已经发送。"));
      },
    );
  };

  const submitReset = (event) => {
    event.preventDefault();
    if (fields.password.length < 12) {
      setError(copy(language, "Password must contain at least 12 characters.", "密码至少需要 12 位。"));
      return;
    }
    if (fields.password !== fields.confirmPassword) {
      setError(copy(language, "Passwords do not match.", "两次输入的密码不一致。"));
      return;
    }
    if (!linkToken) {
      setError(copy(language, "The reset link is missing or invalid.", "重置链接无效，请重新申请。"));
      return;
    }
    runRequest(
      () => confirmPasswordReset(linkToken, fields.password),
      () => {
        window.history.replaceState({}, "", "/");
        setMode("login");
        setFields((current) => ({ ...current, password: "", confirmPassword: "" }));
        setNotice(copy(language, "Password reset. Sign in with your new password.", "密码已重置，请使用新密码登录。"));
      },
    );
  };

  useEffect(() => {
    const path = window.location.pathname;
    const search = new URLSearchParams(window.location.search);
    const token = search.get("token") || "";
    if (path === "/sso/callback") {
      const code = search.get("code") || "";
      const errorCode = search.get("error") || "";
      window.history.replaceState({}, "", "/sso/callback");
      setMode("sso-callback");
      if (errorCode) {
        setError(localizeApiError(
          { code: errorCode },
          language,
          copy(language, "CRM sign-in failed. Open TIDE from CRM again.", "CRM 登录失败，请从 CRM 重新进入。"),
        ));
        return;
      }
      if (!code) {
        setError(copy(language, "The CRM login link is invalid. Open TIDE from CRM again.", "CRM 登录链接无效，请从 CRM 重新进入。"));
        return;
      }
      runRequest(() => exchangeCrmSso(code), onAuthenticated);
      return;
    }
    if (path === "/reset-password") {
      setLinkToken(token);
      setMode("reset");
      if (!token) setError(copy(language, "The reset link is missing or invalid.", "重置链接无效，请重新申请。"));
      return;
    }
    if (path === "/verify-email") {
      setMode("verifying-link");
      if (!token) {
        setError(copy(language, "The verification link is missing or invalid.", "验证链接无效，请重新发送。"));
        return;
      }
      runRequest(
        () => confirmEmail(token),
        () => {
          window.history.replaceState({}, "", "/");
          setMode("login");
          setNotice(copy(language, "Email verified. You can now sign in.", "邮箱验证成功，现在可以登录。"));
        },
      );
    }
  }, []);

  useEffect(() => {
    let active = true;
    getAuthCapabilities()
      .then((result) => {
        if (!active) return;
        setCapabilities(result);
        if (
          result.authMode === "CRM_SSO_ONLY"
          && window.location.pathname !== "/sso/callback"
        ) {
          setMode("sso-only");
        }
      })
      .catch(() => {
        // 能力接口异常时保留现有登录入口，避免 HYBRID 模式被误锁。
      });
    return () => {
      active = false;
    };
  }, []);

  const modeContent = {
    login: {
      title: copy(language, "Account sign in", "教师账号登录"),
      intro: copy(language, "Sign in with your registered teacher account.", "使用你的教师账号登录。"),
    },
    register: {
      title: copy(language, "Create your account", "创建教师账号"),
      intro: copy(language, "Register with your work email, then verify it to continue.", "输入邮箱并设置密码，验证后即可登录。"),
    },
    "verify-pending": {
      title: copy(language, "Check your email", "请查收邮件"),
      intro: copy(language, `Use the verification link sent to ${fields.email}.`, `请打开发送至 ${fields.email} 的验证链接。`),
    },
    "verifying-link": {
      title: copy(language, "Verifying your email", "正在验证邮箱"),
      intro: copy(language, "Please wait while we verify this link.", "正在验证链接，请稍候。"),
    },
    forgot: {
      title: copy(language, "Forgot password", "忘记密码"),
      intro: copy(language, "Verify your email before setting a new password.", "先验证注册邮箱，再设置新密码。"),
    },
    reset: {
      title: copy(language, "Reset password", "重置密码"),
      intro: copy(language, "Create a new password for your teacher account.", "为你的教师账户设置一个新密码。"),
    },
    "reset-pending": {
      title: copy(language, "Check your email", "请查收邮件"),
      intro: copy(language, "Open the password reset link to continue.", "请打开邮件中的密码重置链接。"),
    },
    "sso-callback": {
      title: copy(language, "Signing in from CRM", "正在从 CRM 登录"),
      intro: copy(language, "Please wait while we create your TIDE session.", "正在建立 TIDE 登录状态，请稍候。"),
    },
    "sso-only": {
      title: copy(language, "Open TIDE from CRM", "请从 CRM 进入"),
      intro: copy(language, "Direct account sign-in is no longer available.", "当前仅支持通过 CRM 登录新师训练营。"),
    },
  }[mode];

  return (
    <main className="auth-screen auth-account-screen">
      <section className="auth-story" aria-labelledby="auth-title">
        <div className="auth-brand">
          <img src={publicAsset("/assets/brand/51talk-logo-blue.png")} alt="51Talk" />
          <span>{copy(language, "New Teacher Camp", "新师训练营")}</span>
        </div>
        <h1 id="auth-title" className="sr-only">{copy(language, "New Teacher Camp account access", "新师训练营账户入口")}</h1>
        <img className="auth-toki" src={publicAsset("/assets/toki/wave.png")} alt={copy(language, "Toki welcomes you", "Toki 欢迎你") } />
      </section>

      <section className="auth-panel" aria-label={copy(language, "Account access", "账户入口") }>
        <div className="auth-language" role="group" aria-label={copy(language, "Language", "语言") }>
          <Globe size={18} />
          <button type="button" className={language === "en" ? "active" : ""} onClick={() => onLanguageChange("en")}>EN</button>
          <button type="button" className={language === "zh" ? "active" : ""} onClick={() => onLanguageChange("zh")}>中文</button>
        </div>

        <div className="auth-panel-card auth-account-card">
          <div className="auth-form-brand">
            <img src={publicAsset("/assets/brand/51talk-logo-blue.png")} alt="" />
            <span>{copy(language, "New Teacher Camp", "新师训练营")}</span>
          </div>

          {mode !== "login" && mode !== "register" && (
            <button className="auth-back-button" type="button" onClick={() => switchMode(mode === "reset" ? "forgot" : "login")}>
              <ArrowLeft size={18} />
              {copy(language, "Back", "返回")}
            </button>
          )}

          <div className="auth-heading-block">
            <h2>{modeContent.title}</h2>
            <p>{modeContent.intro}</p>
          </div>

          {notice && <div className="auth-form-notice" role="status"><CheckCircle size={19} weight="fill" />{notice}</div>}
          {error && <div className="auth-form-error" role="alert">{error}</div>}

          {mode === "login" && (
            <form className="auth-form" onSubmit={submitLogin} noValidate>
              <label className="auth-field" htmlFor="login-email">
                <span>{copy(language, "Email", "邮箱")}</span>
                <span className="auth-input-shell"><EnvelopeSimple size={19} /><input id="login-email" type="email" value={fields.email} onChange={updateField("email")} placeholder="name@51talk.com" autoComplete="email" /></span>
              </label>
              <PasswordField id="login-password" label={copy(language, "Password", "密码")} value={fields.password} onChange={updateField("password")} language={language} />
              <button className="auth-forgot-link" type="button" onClick={() => switchMode("forgot")}>{copy(language, "Forgot password?", "忘记密码？")}</button>
              <button className="auth-submit-button" type="submit" disabled={submitting}>{submitting ? copy(language, "Signing in…", "登录中…") : copy(language, "Sign in", "登录")}{!submitting && <ArrowRight size={19} weight="bold" />}</button>
            </form>
          )}

          {mode === "register" && (
            <form className="auth-form" onSubmit={submitRegister} noValidate>
              <label className="auth-field" htmlFor="register-teacher-id"><span>{copy(language, "Teacher ID", "教师编号")}</span><span className="auth-input-shell"><UserCircle size={19} /><input id="register-teacher-id" type="text" value={fields.teacherId} onChange={updateField("teacherId")} placeholder={copy(language, "Your teacher ID", "请输入教师编号")} autoComplete="username" /></span></label>
              <label className="auth-field" htmlFor="register-email"><span>{copy(language, "Work email", "工作邮箱")}</span><span className="auth-input-shell"><EnvelopeSimple size={19} /><input id="register-email" type="email" value={fields.email} onChange={updateField("email")} placeholder="name@51talk.com" autoComplete="email" /></span></label>
              <PasswordField id="register-password" label={copy(language, "Create password", "设置密码")} value={fields.password} onChange={updateField("password")} language={language} autoComplete="new-password" />
              <PasswordField id="register-confirm" label={copy(language, "Confirm password", "确认密码")} value={fields.confirmPassword} onChange={updateField("confirmPassword")} language={language} autoComplete="new-password" />
              <button className="auth-submit-button" type="submit" disabled={submitting}>{submitting ? copy(language, "Creating…", "创建中…") : copy(language, "Create account", "创建账户")} {!submitting && <ArrowRight size={19} weight="bold" />}</button>
            </form>
          )}

          {mode === "forgot" && (
            <form className="auth-form" onSubmit={submitForgot} noValidate>
              <label className="auth-field" htmlFor="forgot-email"><span>{copy(language, "Registered email", "注册邮箱")}</span><span className="auth-input-shell"><EnvelopeSimple size={19} /><input id="forgot-email" type="email" value={fields.email} onChange={updateField("email")} placeholder="name@51talk.com" autoComplete="email" /></span></label>
              <button className="auth-submit-button" type="submit" disabled={submitting}>{submitting ? copy(language, "Sending…", "发送中…") : copy(language, "Send reset link", "发送重置链接")} {!submitting && <ArrowRight size={19} weight="bold" />}</button>
            </form>
          )}

          {mode === "verify-pending" && (
            <div className="auth-form">
              <button className="auth-submit-button" type="button" disabled={submitting} onClick={() => runRequest(() => resendVerification(fields.email.trim()), () => setNotice(copy(language, "If the account is awaiting verification, a new link has been sent.", "如果账号仍待验证，新的验证链接已经发送。")))}>{submitting ? copy(language, "Sending…", "发送中…") : copy(language, "Resend verification link", "重新发送验证链接")}</button>
              <button className="auth-forgot-link" type="button" onClick={() => switchMode("login")}>{copy(language, "Back to sign in", "返回登录")}</button>
            </div>
          )}

          {mode === "verifying-link" && submitting && (
            <div className="auth-form-notice" role="status">{copy(language, "Verifying…", "验证中…")}</div>
          )}

          {mode === "sso-callback" && submitting && (
            <div className="auth-form-notice" role="status">{copy(language, "Signing in…", "登录中…")}</div>
          )}

          {mode === "sso-only" && capabilities?.crmEntryUrl && (
            <div className="auth-form">
              <a className="auth-submit-button" href={capabilities.crmEntryUrl}>
                {copy(language, "Go to CRM", "前往 CRM")}
                <ArrowRight size={19} weight="bold" />
              </a>
            </div>
          )}

          {mode === "reset" && (
            <form className="auth-form" onSubmit={submitReset} noValidate>
              <PasswordField id="reset-password" label={copy(language, "New password", "新密码")} value={fields.password} onChange={updateField("password")} language={language} autoComplete="new-password" />
              <PasswordField id="reset-confirm" label={copy(language, "Confirm new password", "确认新密码")} value={fields.confirmPassword} onChange={updateField("confirmPassword")} language={language} autoComplete="new-password" />
              <button className="auth-submit-button" type="submit" disabled={submitting}>{submitting ? copy(language, "Resetting…", "重置中…") : copy(language, "Reset password", "重置密码")} {!submitting && <ArrowRight size={19} weight="bold" />}</button>
            </form>
          )}

          {mode === "reset-pending" && (
            <div className="auth-form">
              <button className="auth-forgot-link" type="button" onClick={() => switchMode("login")}>{copy(language, "Back to sign in", "返回登录")}</button>
            </div>
          )}

          {(mode === "login" || mode === "register") && (
            <div className="auth-mode-switch">
              <span>{mode === "login" ? copy(language, "New here?", "还没有账户？") : copy(language, "Already registered?", "已经注册？")}</span>
              <button type="button" onClick={() => switchMode(mode === "login" ? "register" : "login")}>
                {mode === "login" ? copy(language, "Create an account", "注册账户") : copy(language, "Back to sign in", "返回登录")}
              </button>
            </div>
          )}

          {mode === "login" && capabilities?.crmEntryUrl && capabilities?.crmSsoEnabled && (
            <div className="auth-mode-switch">
              <span>{copy(language, "Already in CRM?", "已登录 CRM？")}</span>
              <a href={capabilities.crmEntryUrl}>{copy(language, "Open from CRM", "从 CRM 进入")}</a>
            </div>
          )}

          <p className="auth-note"><LockKey size={15} />{copy(language, "Use your company email to access New Teacher Camp.", "请使用公司邮箱登录新师训练营。")}</p>
        </div>
      </section>
    </main>
  );
}
