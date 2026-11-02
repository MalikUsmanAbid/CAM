# USMAN JARVIS — Asaan User Manual

## App kholna

Installer install karke Start Menu se USMAN JARVIS kholein. Portable ZIP ko writable local folder mein extract karke `Start-Portable.cmd` chalayein. ZIP ke andar se app na chalayein. Portable ko installed Microsoft WebView2 chahiye.

Installed aur portable ki settings/data alag hain. Ek mode choose karein. Running database OneDrive ya network folder mein na rakhein.

## Gemini, Claude aur doosri companies ke models

Version 0.1.1 mein **Overview → Provider settings → Desktop model providers** se Google Gemini, Anthropic Claude aur Other compatible cloud API select kar sakte hain. Keys abhi optional hain; bina key cloud unavailable rahega. [Cloud models ka step-by-step guide](CLOUD_MODEL_SETUP_ROMAN_URDU.md) dekhein. Paid usage default disabled hai.

## Aapka installed AI model connect karna

23 October 2026 ko `ollama list` se confirm hua: **usman-jarvis-coder-cpu:latest**, takreeban **986 MB**, pehle se installed hai. Ollama server bhi respond kar raha tha. Dobara download zaroori nahi.

1. JARVIS mein **Overview → Provider settings → Desktop model providers** kholein.
2. Neeche wali values set karein.
3. **Save for next desktop launch** dabayein.
4. Windows system tray mein JARVIS par right-click karke **Exit JARVIS** karein.
5. App dobara kholein aur **Chat** mein chhota sawal bhejein.

| Field | Value |
| --- | --- |
| Provider | Local Ollama |
| Model | usman-jarvis-coder-cpu:latest |
| HTTP numeric loopback endpoint | http://127.0.0.1:11434/v1 |

Provider change karne se Model field default naam par ja sakta hai; Save se pehle exact installed naam check karein. Sirf window ka X dabana full exit nahi: window tray mein hide hoti hai.

**Connected** backend connection hai. AI ka proof successful live response hai. **Mock (local)** ya development response aaye to abhi real model use nahi ho raha. Settings save karna Ollama ko start, download ya verify nahi karta.

## Ollama band ho to

Start Menu se Ollama kholein. Zaroorat par PowerShell mein:

```powershell
ollama serve
```

Agar port already in use aaye aur `ollama list` kaam kare to doosra server start karne ki zaroorat nahi. Command na mile to nayi PowerShell kholein, ya full path use karein:

```powershell
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" list
```

## Doosra model add karna

Official Ollama library se exact naam choose karke PowerShell mein:

```powershell
ollama pull MODEL_NAME
ollama list
```

`MODEL_NAME` ko asli model naam se replace karein. Phir JARVIS ke Model field mein wahi naam set karein, Save aur full restart karein. Endpoint wahi rahega. Is laptop par maujooda CPU model se shuru karein; bade models slow ho sakte hain.

[Official Ollama commands](https://docs.ollama.com/cli).

## Rozmarra istemaal

**Chat:** Roman Urdu, Urdu ya English mein sawal likhein aur Send karein. Chat ka jawab files edit ya commands execute hone ka proof nahi.

**Coding Studio:** Settings mein project directory register karein, workspace selector se select karein aur Coding Studio kholein. File select karke **AI coding request** likhein. **Ask AI for reviewed patch** se proposal lein, **Exact diff** padhein, phir **Request apply approval → Approve exact patch** karein. Test/build commands ko alag exact approval chahiye. Failed command successful completion nahi. Undo ke liye **Request checkpoint rollback** use karein.

**Tasks / Approvals:** task status aur pending approvals dekhein. Approve karne se pehle project, destination aur exact action check karein.

**Memory / Knowledge:** personal/project scope check karein. Har module ki live verification abhi mukammal nahi.

## Problem aur hal

| Problem | Kya karein |
| --- | --- |
| Mock response | Provider/model, Save aur full restart check karein. |
| Model unavailable | Ollama start karein; `ollama list` mein exact selected naam dekhein. |
| Slow response | Unused apps band karein aur ek request complete hone dein. |
| X ke baad app chalti rahe | System tray se Exit JARVIS karein. |
| Portable settings galat | App band karke Reset-Settings.cmd use karein; database retained rehta hai. |
| Migration required | Database preserve karein; backup aur authorized migration karayein. |

## Data aur current limits

Installed data: `%LOCALAPPDATA%\USMANJARVIS`. Portable data aur WebView profile: extracted folder ka `data` directory. Backup/copy se pehle tray se app Exit karein. Executable copy karne se installed aur portable data migrate nahi hota.

Yeh manual r2 release ke liye hai. Packages unsigned hain. Installer/portable launch is device par verified hai; complete M0–M9 product accepted nahi. Clean-machine, tray/recovery aur browser/UIA security acceptance baqi hai. Browser/computer execution disabled hai. Local model select karne se voice ya saare integrations activate nahi hote; voice ko alag configured service chahiye. Account login, publishing aur paid usage ki relevant authorization alag rahegi.

[Release verification](RELEASE_VERIFICATION_2026_10_23.md).
