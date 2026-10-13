"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Loader2, Plus, X } from "lucide-react";
import { useWorkspace } from "@/hooks/use-workspace";
import { api, mutation } from "@/lib/api";
import type { Booking, CallDetail, Location, Resource } from "@/lib/types";
import { LoginScreen } from "./auth-login";
import { DashboardLayout } from "./dashboard-layout";
import { DashboardOverview } from "./dashboard-overview";
import { FloorPlan, TableDetail } from "./floor-plan";
import { CallsPage } from "./calls-page";
import { CallActivityTimeline } from "./call-activity-timeline";
import { ReservationsPage } from "./reservations-page";
import { KitchenOrderBoard, OrderTable } from "./kitchen-order-board";
import { AttentionQueue } from "./attention-queue";
import { ReceptionistPage } from "./receptionist-page";
import { IntegrationsPage } from "./integrations-page";
import { AssistantPage } from "./assistant-page";
import { MenuPage, ServicesPage, StaffPage, WaitlistPage } from "./operations-pages";
import { LocationForm, ResourceForm, ServiceForm, SettingsForm, StaffForm } from "./admin-forms";
import { BookingForm, Empty, OrderForm } from "./workflows";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";

export default function Dashboard({ vertical, section }: { vertical: "restaurant" | "salon"; section: string }) {
  const router = useRouter(); const workspace = useWorkspace(vertical);
  const { auth, locations, locationId, data, attention, voice, error, setError, refresh, load } = workspace;
  const [busy, setBusy] = useState(false); const [toast, setToast] = useState("");
  const [bookingOpen, setBookingOpen] = useState(false); const [editing, setEditing] = useState<Booking>(); const [orderOpen, setOrderOpen] = useState(false);
  const [tableId, setTableId] = useState<string | null>(null); const [callDetail, setCallDetail] = useState<CallDetail | null>(null);
  const [newResource, setNewResource] = useState(false); const [newService, setNewService] = useState(false); const [newLocation, setNewLocation] = useState(false);
  const [staff, setStaff] = useState<Resource | null>(null); const [waitOpen, setWaitOpen] = useState(false); const [waitName, setWaitName] = useState(""); const [waitParty, setWaitParty] = useState(2);
  const [cancel, setCancel] = useState<{ kind: "reservation" | "order"; id: string; name: string } | null>(null);
  useEffect(() => {
    if (!callDetail?.id || !data?.as_of) return;
    let active = true;
    api<CallDetail>(`/calls/${callDetail.id}`).then(value => { if (active) setCallDetail(value); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [callDetail?.id, data?.as_of, setError]);
  async function act(path: string, body?: unknown, method = "POST", message = "Saved") {
    setBusy(true); setError("");
    try { await mutation(path, body, method); setToast(message); await refresh(); return true; }
    catch (e) { setError((e as Error).message); return false; }
    finally { setBusy(false); }
  }
  function completed(message: string) { setToast(message); void refresh(); }
  function openBooking(booking?: Booking) { setEditing(booking); setBookingOpen(true); }
  async function openCall(id: string) { try { setCallDetail(await api<CallDetail>(`/calls/${id}`)); } catch (e) { setError((e as Error).message); } }
  function selectLocation(location: Location) { sessionStorage.setItem(`frontdesk-${location.vertical}-location`, location.id); setTableId(null); setCallDetail(null); if (location.vertical === vertical) workspace.setLocationId(location.id); router.push(`/app/${location.vertical}/overview`); }
  async function logout() { try { await mutation("/auth/logout"); workspace.setAuth("signed_out"); } catch (e) { setError((e as Error).message); } }
  if (auth === "signed_out") return <LoginScreen onLogin={() => { void load(); }} />;
  if (auth === "loading") return <div className="app-loading"><Loader2 size={22} className="spin" /><p>Loading FrontDesk OS…</p></div>;
  const controls = <>
    {["overview", "reservations", "calendar"].includes(section) && <Button variant="outline" onClick={() => openBooking()}><Plus size={14} />{vertical === "restaurant" ? "Create reservation" : "Create appointment"}</Button>}
    {["kitchen", "orders"].includes(section) && <Button variant="outline" onClick={() => setOrderOpen(true)}><Plus size={14} />Create order</Button>}
    {section === "waitlist" && <Button variant="outline" onClick={() => setWaitOpen(true)}><Plus size={14} />Add guest</Button>}
    {["floor", "staff"].includes(section) && !(section === "floor" && data?.location.settings.floor_provider) && <Button variant="outline" onClick={() => setNewResource(true)}><Plus size={14} />{section === "floor" ? "Add table" : "Add stylist"}</Button>}
    {section === "services" && <Button variant="outline" onClick={() => setNewService(true)}><Plus size={14} />Add service</Button>}
  </>;
  return <><DashboardLayout vertical={vertical} section={section} location={data?.location} locations={locations} attentionCount={attention.length} onSelectLocation={selectLocation} onCreateLocation={() => setNewLocation(true)} onLogout={logout} onRefresh={() => { void refresh(); }} onAttention={() => router.push(`/app/${vertical}/attention`)} actions={controls}>
    {!locationId ? <Empty title={`Create your first ${vertical} location`} detail="Add a location to get started." action={<Button onClick={() => setNewLocation(true)}>Create location</Button>} /> : !data ? <div className="loading-panel"><Loader2 size={20} className="spin" />Loading workspace…</div> : <>
      {section === "overview" && <DashboardOverview data={data} attention={attention} voice={voice} onTable={table => setTableId(table.id)} onCall={id => { void openCall(id); }} onUpdated={() => { void refresh(); }} onPause={() => { void act(`/locations/${locationId}`, { intake_paused: !data.location.settings.intake_paused }, "PATCH", data.location.settings.intake_paused ? "New requests resumed" : "New requests paused"); }} />}
      {section === "floor" && <FloorPlan data={data} onSelect={table => setTableId(table.id)} onAdd={data.location.settings.floor_provider ? undefined : () => setNewResource(true)} />}
      {["reservations", "calendar"].includes(section) && <ReservationsPage data={data} busy={busy} onEdit={openBooking} onCancel={booking => setCancel({ kind: "reservation", id: booking.id, name: booking.customer_name })} />}
      {section === "kitchen" && <KitchenOrderBoard key={locationId} data={data} onUpdated={() => { void refresh(); }} busy={busy} onCancel={order => setCancel({ kind: "order", id: order.id, name: order.customer_name })} />}
      {section === "orders" && <OrderTable data={data} busy={busy} onCancel={order => setCancel({ kind: "order", id: order.id, name: order.customer_name })} />}
      {section === "calls" && <CallsPage data={data} onOpen={id => { void openCall(id); }} />}
      {section === "attention" && <AttentionQueue resources={data.resources} items={attention} timezone={data.location.timezone} onCall={id => { void openCall(id); }} onTable={id => setTableId(id)} onUpdated={() => { void refresh(); }} />}
      {section === "voice" && <ReceptionistPage key={locationId} data={data} voice={voice} onUpdated={() => { void refresh(); }} />}
      {section === "assistant" && <AssistantPage key={locationId} data={data} onCall={id => { void openCall(id); }} />}
      {section === "integrations" && <IntegrationsPage key={locationId} data={data} onUpdated={() => { void refresh(); }} />}
      {section === "menu" && <MenuPage data={data} busy={busy} onToggle={(id, available) => { void act(`/menu/${id}`, { available }, "PATCH", "Menu availability updated"); }} />}
      {section === "waitlist" && <WaitlistPage data={data} onUpdated={() => { void refresh(); }} onSeat={() => router.push(`/app/${vertical}/floor`)} />}
      {section === "staff" && <StaffPage data={data} busy={busy} onEdit={setStaff} onToggle={resource => { void act(`/resources/${resource.id}`, { state: resource.state === "available" ? "out_of_service" : "available" }, "PATCH", "Staff availability updated"); }} />}
      {section === "services" && <ServicesPage data={data} busy={busy} onToggle={(id, enabled) => { void act(`/services/${id}`, { enabled }, "PATCH", "Service availability updated"); }} />}
      {section === "settings" && <SettingsForm key={locationId} data={data} busy={busy} save={body => { void act(`/locations/${locationId}`, body, "PATCH", "Business policies saved"); }} create={() => setNewLocation(true)} />}
    </>}
  </DashboardLayout>
  {toast && <div className="toast" role="status"><Check size={14} /><span>{toast}</span><button className="icon-button" onClick={() => setToast("")} aria-label="Dismiss notification"><X size={14} /></button></div>}
  {error && <div className="toast error-toast" role="alert"><span>{error}</span><button className="icon-button" onClick={() => setError("")} aria-label="Dismiss error"><X size={14} /></button></div>}
  {data && <>
    {bookingOpen && <BookingForm key={editing?.id || `new-${locationId}`} data={data} open onClose={() => setBookingOpen(false)} onSuccess={completed} editing={editing} />}
    {orderOpen && <OrderForm data={data} open onClose={() => setOrderOpen(false)} onSuccess={completed} />}
    {tableId && data.resources.some(resource => resource.id === tableId) && <TableDetail key={tableId} data={data} tableId={tableId} onClose={() => setTableId(null)} onUpdated={() => { completed("Table state updated"); }} onReserve={() => { setTableId(null); openBooking(); }} />}
    {callDetail && <Dialog open onClose={() => setCallDetail(null)} title="Call details"  wide><CallActivityTimeline detail={callDetail} timezone={data.location.timezone} /></Dialog>}
    {staff && <StaffForm key={staff.id} staff={staff} data={data} onClose={() => setStaff(null)} save={async body => { if (await act(`/resources/${staff.id}`, body, "PATCH", "Schedule saved")) setStaff(null); }} />}
    {newResource && <ResourceForm data={data} onClose={() => setNewResource(false)} save={async body => { if (await act("/resources", body, "POST", "Resource created")) setNewResource(false); }} />}
    {newService && <ServiceForm data={data} onClose={() => setNewService(false)} save={async body => { if (await act("/services", body, "POST", "Service created")) setNewService(false); }} />}
    <Dialog open={waitOpen} onClose={() => setWaitOpen(false)} title="Add guest to waitlist" ><form className="form-grid" onSubmit={async event => { event.preventDefault(); if (await act("/waitlist", { location_id: locationId, name: waitName, party_size: waitParty }, "POST", "Guest added to waitlist")) { setWaitOpen(false); setWaitName(""); } }}><label>Guest name<input value={waitName} onChange={e => setWaitName(e.target.value)} required maxLength={100} /></label><label>Party size<input type="number" value={waitParty} min={1} max={30} onChange={e => setWaitParty(Number(e.target.value))} /></label><div className="form-actions span-2"><Button disabled={busy} type="submit">Add to waitlist</Button></div></form></Dialog>
  </>}
  {newLocation && <LocationForm vertical={vertical} onClose={() => setNewLocation(false)} save={async body => { try { const created = await mutation<Location>("/locations", body); sessionStorage.setItem(`frontdesk-${vertical}-location`, created.id); setNewLocation(false); await load(); } catch (e) { setError((e as Error).message); } }} />}
  <Dialog open={!!cancel} onClose={() => setCancel(null)} title={`Cancel ${cancel?.kind || "record"}?`} ><p className="muted">Confirm cancellation for {cancel?.name}. </p><div className="form-actions"><Button variant="outline" onClick={() => setCancel(null)}>Keep {cancel?.kind}</Button><Button disabled={busy} onClick={async () => { if (cancel && await act(`/${cancel.kind === "order" ? "orders" : "reservations"}/${cancel.id}`, { status: "cancelled" }, "PATCH", "Cancellation saved")) setCancel(null); }}>Confirm cancellation</Button></div></Dialog>
  </>;
}
