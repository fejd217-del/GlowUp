-- Integration test. Run against a test Supabase after both schemas. Everything rolls back.
begin;
insert into glow_users(id,name) values (900000001,'Crypto test');
insert into glow_crypto_orders(id,user_id,plan,days,amount,recipient,master,expires)
 values('GLOWintegrationtest',900000001,'month',30,15000000,'wallet','master',extract(epoch from now())::bigint+1800);
select glow_crypto_action('deposit',jsonb_build_object('id','test-tx-1','event_id','test-tx-1','order','GLOWintegrationtest','amount',5000000,'recipient','wallet','master','master','sender','sender','chain_time',extract(epoch from now())::bigint));
do $$ begin
 if (select credited from glow_crypto_orders where id='GLOWintegrationtest') then raise exception 'Underpayment granted access';end if;
end $$;
select glow_crypto_action('deposit',jsonb_build_object('id','test-tx-2','event_id','test-tx-2','order','GLOWintegrationtest','amount',10000000,'recipient','wallet','master','master','sender','sender','chain_time',extract(epoch from now())::bigint));
select glow_crypto_action('deposit',jsonb_build_object('id','test-tx-2','event_id','test-tx-2','order','GLOWintegrationtest','amount',10000000,'recipient','wallet','master','master','sender','sender','chain_time',extract(epoch from now())::bigint));
do $$ begin
 if not (select credited from glow_crypto_orders where id='GLOWintegrationtest') then raise exception 'Full payment did not grant access';end if;
 if (select count(*) from glow_grants where id='crypto:GLOWintegrationtest')<>1 then raise exception 'Duplicate grant';end if;
 if (select received from glow_crypto_orders where id='GLOWintegrationtest')<>15000000 then raise exception 'Duplicate deposit';end if;
end $$;
select glow_crypto_action('refund_record','{"order":"GLOWintegrationtest","actor":900000001,"tx":"already-sent-refund"}'::jsonb);
do $$ begin
 if not (select revoked from glow_grants where id='crypto:GLOWintegrationtest') then raise exception 'Refund did not revoke grant';end if;
end $$;
rollback;
