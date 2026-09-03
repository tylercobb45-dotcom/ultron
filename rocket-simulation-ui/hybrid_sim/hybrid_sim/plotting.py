"""Engine and trajectory dashboards (Agg backend, saves PNG)."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def plot_engine(res, path="engine.png", title="Hybrid engine"):
    t = res["t"]
    fig, ax = plt.subplots(2, 2, figsize=(11, 7))
    fig.suptitle(title, fontsize=13, fontweight="bold")
    ax[0,0].plot(t, res["thrust"], color="#c0392b", lw=1.8)
    ax[0,0].set(xlabel="Time (s)", ylabel="Thrust (N)", title="Thrust")
    ax[0,1].plot(t, res["Pc"]/1e6, color="#2c3e50", lw=1.8, label="chamber")
    ax[0,1].plot(t, res["P_tank"]/1e6, "--", color="#7f8c8d", lw=1.2, label="tank")
    ax[0,1].set(xlabel="Time (s)", ylabel="Pressure (MPa)", title="Pressure"); ax[0,1].legend()
    ax[1,0].plot(t, res["OF"], color="#2980b9", lw=1.8)
    ax[1,0].set(xlabel="Time (s)", ylabel="O/F", title="Mixture ratio")
    ax[1,1].plot(t, res["mdot_ox"], color="#16a085", lw=1.5, label="oxidizer")
    ax[1,1].plot(t, res["mdot_fuel"], color="#e67e22", lw=1.5, label="fuel")
    ax[1,1].set(xlabel="Time (s)", ylabel="mdot (kg/s)", title="Mass flow"); ax[1,1].legend()
    for a in ax.flat: a.grid(alpha=0.3)
    fig.tight_layout(rect=[0,0,1,0.96]); fig.savefig(path, dpi=120); plt.close(fig)
    return path

def plot_flight(fl, path="flight.png", title="Trajectory"):
    t = fl["t"]
    fig, ax = plt.subplots(2, 2, figsize=(11, 7))
    fig.suptitle(title, fontsize=13, fontweight="bold")
    ax[0,0].plot(t, fl["altitude"]*3.28084, color="#8e44ad", lw=1.8)
    ax[0,0].set(xlabel="Time (s)", ylabel="Altitude (ft)", title="Altitude")
    ax[0,1].plot(t, fl["velocity"], color="#27ae60", lw=1.8); ax[0,1].axhline(0, color="k", lw=0.6)
    ax[0,1].set(xlabel="Time (s)", ylabel="Velocity (m/s)", title="Velocity")
    ax[1,0].plot(t, fl["mach"], color="#d35400", lw=1.8)
    ax[1,0].set(xlabel="Time (s)", ylabel="Mach", title="Mach number")
    ax[1,1].plot(t, fl["accel"]/9.80665, color="#c0392b", lw=1.5)
    ax[1,1].set(xlabel="Time (s)", ylabel="Acceleration (g)", title="Acceleration")
    for a in ax.flat: a.grid(alpha=0.3)
    fig.tight_layout(rect=[0,0,1,0.96]); fig.savefig(path, dpi=120); plt.close(fig)
    return path
