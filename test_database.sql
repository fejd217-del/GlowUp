-- Optional integration check: run AFTER schema.sql in Supabase SQL Editor.
-- Everything rolls back, including test payments. No Telegram requests are made.
begin;
insert into public.glow_users(id) values(900000000000001);
insert into public.glow_orders(id,user_id,plan,stars,days) values('integration-order-1',900000000000001,'month',10,30);
select public.glow_action('payment','{"uid":900000000000001,"order":"integration-order-1","stars":10,"currency":"XTR","charge":"integration-charge-1"}');
do $$ declare first_expiry bigint;second_expiry bigint; begin
 select access_until into first_expiry from public.glow_users where id=900000000000001;
 perform public.glow_action('payment','{"uid":900000000000001,"order":"integration-order-1","stars":10,"currency":"XTR","charge":"integration-charge-1"}');
 select access_until into second_expiry from public.glow_users where id=900000000000001;
 if first_expiry<>second_expiry then raise exception 'Duplicate payment extended access twice'; end if;
 if first_expiry<extract(epoch from now())::bigint+29*86400 then raise exception 'Missing access'; end if;
 begin
  perform public.glow_action('payment','{"uid":900000000000001,"order":"integration-order-1","stars":11,"currency":"XTR","charge":"integration-charge-2"}');
  raise exception 'Wrong payment accepted';
 exception when others then
  if sqlerrm<>'PAYMENT_MISMATCH' then raise; end if;
 end;
 perform public.glow_action('refund','{"uid":900000000000001,"charge":"integration-charge-1"}');
 if (select access_until from public.glow_users where id=900000000000001)<>0 then raise exception 'Refund left access active'; end if;
 perform public.glow_action('grant','{"uid":900000000000001,"id":"integration-grant","days":null,"actor":1}');
 if not(select lifetime from public.glow_users where id=900000000000001) then raise exception 'Lifetime grant failed'; end if;
 perform public.glow_action('revoke','{"uid":900000000000001,"actor":1}');
 if (select lifetime from public.glow_users where id=900000000000001) then raise exception 'Revoke failed'; end if;
 if has_table_privilege('anon','public.glow_users','SELECT') then raise exception 'Anonymous access is enabled'; end if;
 if has_function_privilege('anon','public.glow_action(text,jsonb)','EXECUTE') then raise exception 'Anonymous RPC is enabled'; end if;
end $$;
rollback;
